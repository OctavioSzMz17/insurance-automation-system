"""Mutation handler — the ONLY place that calls @write_restricted tools (Rule 6).

Contract:
1. Caller (the /confirm HTTP handler) hands us:
   - a verified ClaimProposal from the analyst (carrying the TriageReport)
   - a verified ClaimDecision from the auditor (APPROVED | REJECTED | MANUAL_REVIEW)
   - the approving caller_sub / caller_role
2. We:
   - re-verify both HMAC signatures (defense-in-depth — we don't trust that the caller verified).
   - decide the write set:
       APPROVED      → create_claim(status=APPROVED) + update with decision_metadata
       REJECTED      → create_claim(status=REJECTED) + decision_metadata
       MANUAL_REVIEW → create_claim(status=MANUAL_REVIEW); no further transitions allowed.
   - call the writes via the pool with allow_writes=True (toolregistry double-check).
   - log a full audit entry per Rule 6 (timestamp, approver, model, proposal hashes, outcome).

The handler is intentionally small. No LLM, no looping, no branching beyond the decision
mapping. Everything that could be wrong is verified up front.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from packages.common.exceptions import BusinessError, ForbiddenError, MCPClientError
from packages.common.schemas.claim import ClaimDecision, ClaimProposal
from packages.common.schemas.triage import TriageReport

from core import proposal as proposal_mod
from core.config_runtime import OrchestratorRuntimeView
from core.logging_bridge import get_logger
from core.mcp_client import MCPClientPool
from core.tools import dispatch

_logger = get_logger(__name__)


@dataclass(frozen=True)
class ApprovalContext:
    approver_sub: str
    approver_role: str
    submitter_sub: str   # the original /analyze caller — server-derived, NEVER from request body
    claimant_sub: str | None = None  # the insured client the claim is filed for
    claim_id: str | None = None      # the durable claim row created at intake (analyze)


@dataclass
class MutationResult:
    persisted: bool
    claim: dict[str, Any] | None
    reason: str | None = None


class MutationHandler:
    def __init__(self, *, runtime: OrchestratorRuntimeView, pool: MCPClientPool) -> None:
        self._runtime = runtime
        self._pool = pool

    async def apply(
        self,
        *,
        approval: ApprovalContext,
        analyst_proposal: ClaimProposal,
        analyst_triage: TriageReport,
        auditor_proposal: ClaimProposal,
        auditor_decision: ClaimDecision,
    ) -> MutationResult:
        # 1. Signatures — verify_proposal raises ValidationFailure on any tampering.
        from packages.common.exceptions import ValidationFailure
        try:
            proposal_mod.verify_proposal(
                analyst_proposal,
                payload=analyst_triage.model_dump(mode="json"),
                key=self._runtime.hmac_proposal_key,
            )
        except ValidationFailure as exc:
            raise ForbiddenError(f"analyst proposal: {exc}") from exc
        try:
            proposal_mod.verify_proposal(
                auditor_proposal,
                payload=auditor_decision.model_dump(mode="json"),
                key=self._runtime.hmac_proposal_key,
            )
        except ValidationFailure as exc:
            raise ForbiddenError(f"auditor proposal: {exc}") from exc

        # 2. Pipeline coherence: analyst, auditor, decision and triage must all agree.
        if analyst_proposal.pipeline_id != auditor_proposal.pipeline_id:
            raise ForbiddenError("analyst and auditor proposals refer to different pipelines")
        if auditor_decision.claim_id != analyst_proposal.pipeline_id:
            raise ForbiddenError("decision.claim_id does not match the pipeline")
        if analyst_triage.pipeline_id != analyst_proposal.pipeline_id:
            raise ForbiddenError("analyst triage refers to a different pipeline than the proposal")

        # 3. ACL: ADMIN may approve anyone's claim; otherwise approver must equal submitter.
        if approval.approver_role.upper() != "ADMIN" and approval.approver_sub != approval.submitter_sub:
            raise ForbiddenError("approver is not the original submitter")

        # 4. Audit packet for decision_metadata (Rule 6 — immutable, full provenance).
        audit_payload: dict[str, Any] = {
            "approver_sub": approval.approver_sub,
            "approver_role": approval.approver_role,
            "submitter_sub": approval.submitter_sub,
            "analyst_proposal_id": str(analyst_proposal.proposal_id),
            "analyst_signature": analyst_proposal.signature_hex,
            "auditor_proposal_id": str(auditor_proposal.proposal_id),
            "auditor_signature": auditor_proposal.signature_hex,
            "decision": auditor_decision.decision,
            "decision_reason": auditor_decision.reason,
            "decision_evidence": list(auditor_decision.evidence),
            "model": auditor_decision.model,
            "model_version": auditor_decision.model_version,
            "decided_at": auditor_decision.decided_at.isoformat(),
        }

        # 5. The write — through the registry, allow_writes=True triggers the second-line check.
        #    The claim already exists from intake (created at /analyze, AWAITING_INPUT):
        #    advance its status to the audited decision, preserving the analysis columns.
        #    If there is no intake row (analyze persist failed), create it now as a fallback.
        try:
            if approval.claim_id:
                row = await dispatch(
                    self._pool, "claims__update_claim_status",
                    {
                        "claim_id": approval.claim_id,
                        "new_status": auditor_decision.decision,
                        "decision_metadata": audit_payload,
                        "caller_sub": approval.approver_sub,
                        "caller_role": approval.approver_role,
                    },
                    allow_writes=True,
                )
            else:
                row = await dispatch(
                    self._pool, "claims__create_claim",
                    {
                        "submitted_by_sub": approval.submitter_sub,
                        "claimant_sub": approval.claimant_sub,
                        "policy_id": str(analyst_triage.policy_id),
                        "pdf_sha256": analyst_triage.pdf_sha256,
                        "reported_damage_amount": str(analyst_triage.reported_damage_amount),
                        "severity": analyst_triage.severity.value,
                        "status": auditor_decision.decision,
                        "decision_metadata": audit_payload,
                        "caller_sub": approval.approver_sub,
                        "caller_role": approval.approver_role,
                    },
                    allow_writes=True,
                )
        except (BusinessError, MCPClientError) as exc:
            _logger.warning(
                "mutation refused",
                extra={"event": "mutation.refused", "reason": str(exc),
                       "pipeline_id": str(auditor_decision.claim_id)},
            )
            return MutationResult(persisted=False, claim=None, reason=str(exc))

        _logger.info(
            "claim persisted",
            extra={
                "event": "mutation.persisted",
                "pipeline_id": str(auditor_decision.claim_id),
                "approver_sub": approval.approver_sub,
                "decision": auditor_decision.decision,
                "analyst_proposal_id": str(analyst_proposal.proposal_id),
                "auditor_proposal_id": str(auditor_proposal.proposal_id),
            },
        )
        claim = (row or {}).get("claim") if isinstance(row, dict) else None
        return MutationResult(persisted=True, claim=claim)
