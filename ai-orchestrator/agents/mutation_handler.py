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

from core import proposal as proposal_mod
from core.config_runtime import OrchestratorRuntimeView
from core.logging_bridge import get_logger
from core.mcp_client import MCPClientPool
from core.service_identity import verification_identity
from core.tools import dispatch
from core.verification import (
    build_review_flags,
    evaluate_amount,
    evaluate_coverage,
    evaluate_identity,
    evaluate_policy_ownership,
    unverified,
)
from observability import get_tracer
from packages.common.exceptions import BusinessError, ForbiddenError, MCPClientError
from packages.common.schemas.claim import ClaimDecision, ClaimProposal
from packages.common.schemas.triage import TriageReport

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

        # 4. The deterministic gate. Re-derived HERE, at the write boundary, rather than
        #    read from the HITL payload: that payload is unsigned state in Redis, so a
        #    gate trusting it could be neutralised by whoever can reach Redis. Recomputing
        #    costs three MCP reads and is the one place the check cannot be bypassed.
        async with get_tracer().span(
            "mutation.gate",
            pipeline_id=str(auditor_decision.claim_id),
            decision=auditor_decision.decision,
        ) as gate_span:
            verification = await self._reverify(approval, analyst_triage)
            review_flags = build_review_flags(**verification)
            blocked = bool(review_flags) and auditor_decision.decision == "APPROVED"
            gate_span.set_attributes(flags=len(review_flags), blocked=blocked)
            gate_span.score("gate_blocked", blocked)
            if review_flags:
                gate_span.set_status(not blocked, message=" ".join(review_flags))

        if blocked:
            _logger.warning(
                "approval blocked by deterministic verification",
                extra={
                    "event": "mutation.blocked",
                    "pipeline_id": str(auditor_decision.claim_id),
                    "approver_sub": approval.approver_sub,
                    "flags": review_flags,
                },
            )
            return MutationResult(
                persisted=False,
                claim=None,
                reason=(
                    "La verificación determinística impide aprobar este siniestro: "
                    + " ".join(review_flags)
                ),
            )

        # 5. Audit packet for decision_metadata (Rule 6 — immutable, full provenance).
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
            # The verification as it stood at the moment of the write, not at intake.
            "verification_at_write": verification,
            "review_flags_at_write": review_flags,
        }

        # 6. The write — through the registry, allow_writes=True triggers the second-line check.
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

    # ── deterministic re-verification ───────────────────────────────────────

    async def _reverify(
        self, approval: ApprovalContext, triage: TriageReport
    ) -> dict[str, Any]:
        """Recompute the deterministic checks from source, at approval time.

        Every read is attributed to the verification service principal, not to the
        approver: this is the system establishing facts, and it must not inherit (or be
        limited by) whoever happens to be clicking approve.

        Failures become `unverified` blocks rather than `None`, so a timeout or an
        unreachable MCP raises a review flag and blocks the approval instead of waving
        it through. The gate fails closed by construction.

        When `claimant_sub` is absent the claim is a legacy self-submission with no
        nominated client, so the identity and ownership questions do not apply and their
        blocks stay `None`.
        """
        identity_args = verification_identity()

        policy: dict[str, Any] | None = None
        policy_error: str | None = None
        try:
            res = await self._pool.call(
                "policies", "get_policy_by_id",
                {"policy_id": str(triage.policy_id), **identity_args},
            )
            policy = res.get("policy") or {}
        except Exception as exc:  # noqa: BLE001 — any failure must fail closed
            policy_error = str(exc)

        claimant: dict[str, Any] | None = None
        claimant_error: str | None = None
        if approval.claimant_sub:
            try:
                res = await self._pool.call(
                    "users", "get_user_by_email",
                    {"email": approval.claimant_sub, **identity_args},
                )
                claimant = res.get("user") or {}
            except Exception as exc:  # noqa: BLE001
                claimant_error = str(exc)

        pdf_text: str | None = None
        pdf_error: str | None = None
        try:
            res = await self._pool.call(
                "documents", "extract_text",
                {"pipeline_id": str(triage.pipeline_id), **identity_args},
            )
            pdf_text = res.get("text") or ""
        except Exception as exc:  # noqa: BLE001
            pdf_error = str(exc)

        coverage = (
            evaluate_coverage(policy) if policy is not None
            else unverified(f"No se pudo releer la póliza: {policy_error}")
        )

        ownership: dict[str, Any] | None = None
        identity: dict[str, Any] | None = None
        if approval.claimant_sub:
            if claimant is None:
                reason = f"No se pudo releer al asegurado: {claimant_error}"
                ownership = unverified(reason)
                identity = unverified(reason)
            else:
                ownership = (
                    evaluate_policy_ownership(policy, claimant_client_id=claimant.get("id"))
                    if policy is not None
                    else unverified(f"No se pudo releer la póliza: {policy_error}")
                )
                identity = (
                    evaluate_identity(claimant.get("full_name"), pdf_text)
                    if pdf_text is not None
                    else unverified(f"No se pudo releer el documento: {pdf_error}")
                )

        amount = (
            evaluate_amount(triage.reported_damage_amount, pdf_text)
            if pdf_text is not None
            else unverified(f"No se pudo releer el documento: {pdf_error}")
        )

        return {"coverage": coverage, "ownership": ownership, "identity": identity, "amount": amount}
