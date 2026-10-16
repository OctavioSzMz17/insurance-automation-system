"""Agent pipeline routes: analyze → audit → pending → confirm.

All endpoints require a valid JWT. caller_sub and caller_role are
extracted from the token — not accepted in the request body.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from agents.analyst import RunContext
from agents.auditor import AuditRequest
from agents.mutation_handler import ApprovalContext
from auth import Role, UserPrincipal, require_role
from core import proposal as proposal_mod
from core.hitl import HITLStore
from core.tools import dispatch
from core.service_identity import verification_identity
from observability import Span, SpanKind, get_tracer
from core.verification import (
    build_review_flags,
    evaluate_amount,
    evaluate_coverage,
    evaluate_identity,
    evaluate_policy_ownership,
    unverified,
)
from packages.common.exceptions import ForbiddenError, NotFoundError
from packages.common.schemas.claim import ClaimDecision, ClaimProposal
from packages.common.schemas.rehydrate import from_stored
from packages.common.schemas.triage import TriageReport

logger = logging.getLogger(__name__)
router = APIRouter(tags=["agents"])


# The deterministic checks live in core/verification.py — pure functions, no I/O, so
# they can be unit-tested and exercised by the adversarial eval suite (evals/).


# ── Request / response models ─────────────────────────────────────────────────


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pipeline_id: UUID
    # Email of the client this claim is filed on behalf of. When omitted the
    # caller is treated as the claimant (legacy self-submission).
    claimant_sub: str | None = None


class ClientsResponse(BaseModel):
    clients: list[dict[str, Any]]


class AnalyzeResponse(BaseModel):
    status: str
    steps_used: int
    proposal_id: str | None = None
    proposal: dict[str, Any] | None = None
    triage: dict[str, Any] | None = None
    reason: str | None = None


class AuditRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: UUID


class AuditResponse(BaseModel):
    status: str
    steps_used: int
    decision_id: str | None = None
    decision: dict[str, Any] | None = None
    signed_proposal: dict[str, Any] | None = None
    reason: str | None = None


class PendingProposalSummary(BaseModel):
    """Flattened view of a pending proposal for the monitoring table."""
    proposal_id: str
    pipeline_id: str | None = None
    claimant_sub: str | None = None
    policy_id: str | None = None
    reported_damage_amount: str | None = None
    severity: str | None = None
    summary: str | None = None
    created_at: str | None = None
    coverage: dict[str, Any] | None = None
    ownership: dict[str, Any] | None = None
    identity: dict[str, Any] | None = None
    amount: dict[str, Any] | None = None


class PendingResponse(BaseModel):
    proposals: list[PendingProposalSummary]
    decisions: list[str]


class ClaimsListResponse(BaseModel):
    """Persisted claims from Postgres for the live monitoring board."""
    claims: list[dict[str, Any]]


class ProposalDetailResponse(BaseModel):
    """Full proposal for the HITL review screen (Redis live, or Postgres durable)."""
    proposal_id: str
    # The Redis proposal id used to actually audit/confirm. None once the TTL has
    # expired (the signed proposal is gone) — the screen is then read-only.
    audit_proposal_id: str | None = None
    source: str = "redis"
    pipeline_id: str | None = None
    claimant_sub: str | None = None
    coverage: dict[str, Any] | None = None
    ownership: dict[str, Any] | None = None
    identity: dict[str, Any] | None = None
    amount: dict[str, Any] | None = None
    auditor: dict[str, Any] | None = None
    audit_decision_id: str | None = None
    triage: dict[str, Any] | None = None
    proposal: dict[str, Any] | None = None


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_id: UUID
    approval: Literal["approve", "reject"]


class ConfirmResponse(BaseModel):
    status: Literal["persisted", "rejected_by_human", "refused"]
    claim: dict[str, Any] | None = None
    reason: str | None = None


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/clients", response_model=ClientsResponse)
async def clients(
    request: Request,
    user: UserPrincipal = Depends(require_role(Role.ADMIN)),
) -> ClientsResponse:
    """List insured clients so an operator can pick the claimant. ADMIN only."""
    pool = request.app.state.pool
    result = await pool.call(
        "users", "list_clients", {"caller_sub": user.sub, "caller_role": user.role.value}
    )
    return ClientsResponse(clients=result.get("clients", []))


@router.get("/pdf/{pipeline_id}")
async def get_pdf(
    pipeline_id: UUID,
    request: Request,
    user: UserPrincipal = Depends(require_role(Role.ANALYST)),
) -> FileResponse:
    """Stream the original claim PDF for the in-page viewer (auth-protected)."""
    settings = request.app.state.settings
    upload_dir = Path(settings.claim_upload_dir).resolve()
    dest = (upload_dir / f"{pipeline_id}.pdf").resolve()
    # Path-traversal guard: the resolved file must stay inside the upload dir.
    if not dest.is_relative_to(upload_dir) or not dest.is_file():
        raise HTTPException(status_code=404, detail="document not found")
    return FileResponse(dest, media_type="application/pdf", filename=f"{pipeline_id}.pdf")


async def _trace_verification(
    *,
    coverage: dict[str, Any] | None,
    ownership: dict[str, Any] | None,
    identity: dict[str, Any] | None,
    amount: dict[str, Any] | None,
    review_flags: list[str],
) -> None:
    """Emit one span per deterministic check.

    These are emitted after the fact rather than wrapped around the MCP reads: the
    checks themselves are microseconds, and what a reader (or the live graph UI) wants
    from them is the verdict, not a duration.
    """
    tracer = get_tracer()
    blocks = (
        ("coverage", coverage, "in_force"),
        ("ownership", ownership, "belongs"),
        ("identity", identity, "match"),
        ("amount", amount, "supported"),
    )
    async with tracer.span("verification", flags=len(review_flags)) as parent:
        for name, block, verdict_key in blocks:
            if block is None:
                continue
            async with tracer.span(f"check.{name}", kind=SpanKind.EVENT) as child:
                child.set_attributes(**{k: v for k, v in block.items() if k != "reason"})
                inconclusive = block.get("verified") is False
                if inconclusive or block.get(verdict_key) is False:
                    child.set_status(False, message=str(block.get("reason") or ""))
        parent.score("review_flags", len(review_flags))
        if review_flags:
            parent.set_status(False, message=" ".join(review_flags))


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(
    req: AnalyzeRequest,
    request: Request,
    user: UserPrincipal = Depends(require_role(Role.ANALYST)),
) -> AnalyzeResponse:
    """Root of a claim's trace.

    The body lives in `_analyze` so the span can wrap the whole pipeline without
    re-indenting it — the handler is long, and an indentation-only diff over a hundred
    lines hides the actual change from review.
    """
    async with get_tracer().trace(
        "claim.analyze",
        session_id=str(req.pipeline_id),
        pipeline_id=str(req.pipeline_id),
        claimant_sub=req.claimant_sub,
        operator_sub=user.sub,
        operator_role=user.role.value,
    ) as root:
        response = await _analyze(req, request, user, root)
        root.set_attributes(outcome=response.status, steps_used=response.steps_used)
        if response.status != "success":
            root.set_status(False, message=response.reason or "manual review")
        return response


async def _analyze(
    req: AnalyzeRequest, request: Request, user: UserPrincipal, root: Span
) -> AnalyzeResponse:
    app_state = request.app.state
    ctx = RunContext(
        pipeline_id=req.pipeline_id,
        caller_sub=user.sub,
        caller_role=user.role.value,
        claimant_sub=req.claimant_sub,
    )
    outcome = await app_state.analyst.run(ctx)

    if outcome.status != "success":
        return AnalyzeResponse(status="manual_review", steps_used=outcome.steps_used, reason=outcome.reason)

    proposal_dump = outcome.proposal.model_dump(mode="json") if outcome.proposal else None
    triage_dump = outcome.triage.model_dump(mode="json") if outcome.triage else None
    proposal_id = str(outcome.proposal.proposal_id)

    # ── Deterministic verification (Rule 4). Every read below is attributed to the
    #    verification service principal, not to the operator: these are the system's
    #    own facts, and binding them to the caller's permissions made the anti-
    #    impersonation check silently unavailable to an ANALYST operator.
    #    A failed check records `unverified` rather than None, so it raises a review
    #    flag instead of being indistinguishable from a clean result. The same report
    #    is recomputed at the write boundary by MutationHandler, which is where it
    #    becomes blocking — here it informs the auditor and the HITL screen. ──
    identity_args = verification_identity()

    policy: dict[str, Any] | None = None
    coverage: dict[str, Any] | None = None
    policy_id = (triage_dump or {}).get("policy_id")
    if policy_id:
        try:
            pol_res = await app_state.pool.call(
                "policies", "get_policy_by_id", {"policy_id": policy_id, **identity_args},
            )
            policy = pol_res.get("policy") or {}
            coverage = evaluate_coverage(policy)
        except Exception as exc:  # noqa: BLE001
            logger.warning("coverage check failed | policy_id=%s err=%s", policy_id, exc)
            coverage = unverified(f"No se pudo verificar la vigencia de la póliza: {exc}")

    pdf_text: str | None = None
    pdf_error: str | None = None
    try:
        doc = await app_state.pool.call(
            "documents", "extract_text", {"pipeline_id": str(req.pipeline_id), **identity_args},
        )
        pdf_text = doc.get("text") or ""
    except Exception as exc:  # noqa: BLE001
        pdf_error = str(exc)
        logger.warning("pdf text fetch failed | err=%s", exc)

    # ── Identity: does the document actually name the selected claimant? Catches
    #    filing on behalf of the wrong client. Ownership: does the resolved policy
    #    belong to that claimant? Catches booking the claim against a stranger's
    #    coverage, which identity and coverage both miss. ──
    identity: dict[str, Any] | None = None
    ownership: dict[str, Any] | None = None
    if req.claimant_sub:
        try:
            usr = await app_state.pool.call(
                "users", "get_user_by_email", {"email": req.claimant_sub, **identity_args},
            )
            claimant = usr.get("user") or {}
            identity = (
                evaluate_identity(claimant.get("full_name"), pdf_text)
                if pdf_text is not None
                else unverified(f"No se pudo leer el documento: {pdf_error}")
            )
            if policy is not None:
                ownership = evaluate_policy_ownership(
                    policy, claimant_client_id=claimant.get("id")
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("identity check failed | claimant=%s err=%s", req.claimant_sub, exc)
            reason = f"No se pudo verificar al asegurado: {exc}"
            identity = unverified(reason)
            ownership = unverified(reason)

    # ── Amount: is the reported figure actually in the document? Catches a
    #    hallucinated monetary value. ──
    amount: dict[str, Any] | None = None
    if triage_dump:
        amount = (
            evaluate_amount(triage_dump.get("reported_damage_amount"), pdf_text)
            if pdf_text is not None
            else unverified(f"No se pudo leer el documento: {pdf_error}")
        )

    review_flags = build_review_flags(
        coverage=coverage, ownership=ownership, identity=identity, amount=amount
    )
    await _trace_verification(
        coverage=coverage, ownership=ownership, identity=identity,
        amount=amount, review_flags=review_flags,
    )
    root.set_attributes(review_flags=len(review_flags))

    # ── Auto-audit: run the auditor agent now (not only at confirm) so its
    #    verdict is visible in HITL. It receives the verification report as
    #    GROUND TRUTH. Best-effort — a failure never blocks the analysis. ──
    auditor_result: dict[str, Any] | None = None
    audit_decision_id: str | None = None
    _audit_store: dict[str, Any] | None = None  # signed-decision payload, stored after claim_id is known
    try:
        ao = await app_state.auditor.run(AuditRequest(
            pipeline_id=outcome.proposal.pipeline_id,
            caller_sub=user.sub,
            caller_role=user.role.value,
            triage=outcome.triage,
            analyst_proposal=outcome.proposal,
            verification={"coverage": coverage, "ownership": ownership,
                          "identity": identity, "amount": amount},
        ))
        if ao.status == "success" and ao.decision and ao.signed_proposal:
            audit_decision_id = str(ao.signed_proposal.proposal_id)
            auditor_result = {
                "decision": ao.decision.decision,
                "reason": ao.decision.reason,
                "evidence": list(ao.decision.evidence),
                "decision_id": audit_decision_id,
            }
            _audit_store = {
                "auditor_proposal": ao.signed_proposal.model_dump(mode="json"),
                "auditor_decision": ao.decision.model_dump(mode="json"),
            }
        else:
            auditor_result = {"decision": "MANUAL_REVIEW", "reason": ao.reason or "El auditor no concluyó.",
                              "evidence": [], "decision_id": None}
    except Exception as exc:  # noqa: BLE001 — best-effort, never blocks triage
        logger.warning("auto-audit failed | err=%s", exc)

    # ── Persist the analysis at intake (status AWAITING_INPUT) so it is durable
    #    in Postgres regardless of refresh, TTL expiry, or the HITL decision. ──
    claim_id: str | None = None
    if triage_dump:
        try:
            created = await dispatch(
                app_state.pool, "claims__create_claim",
                {
                    "submitted_by_sub": user.sub,
                    "claimant_sub": req.claimant_sub,
                    "policy_id": triage_dump["policy_id"],
                    "pdf_sha256": triage_dump["pdf_sha256"],
                    "reported_damage_amount": str(triage_dump["reported_damage_amount"]),
                    "severity": triage_dump["severity"],
                    "status": "AWAITING_INPUT",
                    "review_flags": review_flags,
                    "evidence_quotes": triage_dump.get("evidence_quotes") or [],
                    "ai_summary": triage_dump.get("summary"),
                    # Link back to the Redis proposal + keep the structured checks so the
                    # HITL screen can be fully rebuilt from Postgres after the TTL expires.
                    "decision_metadata": {
                        "proposal_id": proposal_id,
                        "pipeline_id": str(req.pipeline_id),
                        "coverage": coverage,
                        "ownership": ownership,
                        "identity": identity,
                        "amount": amount,
                        "auditor": auditor_result,
                    },
                    "caller_sub": user.sub,
                    "caller_role": user.role.value,
                },
                allow_writes=True,
            )
            claim_id = ((created or {}).get("claim") or {}).get("id")
        except Exception as exc:  # noqa: BLE001 — persistence is best-effort, never blocks triage
            logger.warning("intake persist failed | err=%s", exc)

    # Store the signed auditor decision so HITL can confirm/reject WITHOUT re-auditing.
    if _audit_store and audit_decision_id:
        try:
            await app_state.hitl.put_decision(
                decision_id=audit_decision_id,
                owner_sub=user.sub,
                payload={
                    "owner_sub": user.sub,
                    "submitter_sub": user.sub,
                    "claimant_sub": req.claimant_sub,
                    "claim_id": claim_id,
                    "analyst_proposal": proposal_dump,
                    "analyst_triage": triage_dump,
                    **_audit_store,
                },
            )
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning("auto-audit put_decision failed | err=%s", exc)
            audit_decision_id = None

    await app_state.hitl.put_proposal(
        proposal_id=proposal_id,
        owner_sub=user.sub,
        payload={
            "owner_sub": user.sub,
            "pipeline_id": str(req.pipeline_id),
            "claimant_sub": req.claimant_sub,
            "claim_id": claim_id,
            "coverage": coverage,
            "ownership": ownership,
            "identity": identity,
            "amount": amount,
            "auditor": auditor_result,
            "audit_decision_id": audit_decision_id,
            "proposal": proposal_dump,
            "triage": triage_dump,
        },
    )
    return AnalyzeResponse(
        status="success",
        steps_used=outcome.steps_used,
        proposal_id=proposal_id,
        proposal=proposal_dump,
        triage=triage_dump,
    )


@router.post("/audit", response_model=AuditResponse)
async def audit(
    req: AuditRequestBody,
    request: Request,
    user: UserPrincipal = Depends(require_role(Role.AUDITOR)),
) -> AuditResponse:
    async with get_tracer().trace(
        "claim.audit", proposal_id=str(req.proposal_id), operator_sub=user.sub,
    ) as root:
        response = await _audit(req, request, user, root)
        root.set_attributes(outcome=response.status, steps_used=response.steps_used)
        return response


async def _audit(
    req: AuditRequestBody, request: Request, user: UserPrincipal, root: Span
) -> AuditResponse:
    app_state = request.app.state
    try:
        stored = await app_state.hitl.get_proposal(
            str(req.proposal_id), caller_sub=user.sub, caller_role=user.role.value
        )
    except (NotFoundError, ForbiddenError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, NotFoundError) else 403, detail=str(exc))

    # Rehydrated non-strictly: these came back from Redis as JSON, so UUIDs, timestamps
    # and decimals are strings. Field constraints are still enforced — see from_stored.
    proposal = from_stored(ClaimProposal, stored["proposal"])
    triage = from_stored(TriageReport, stored["triage"])
    # Now that the pipeline is known, join this claim's timeline.
    root.set_session(str(proposal.pipeline_id))
    root.set_attributes(pipeline_id=str(proposal.pipeline_id))

    from packages.common.exceptions import ValidationFailure
    try:
        proposal_mod.verify_proposal(
            proposal,
            payload=triage.model_dump(mode="json"),
            key=app_state.settings.hmac_proposal_key,
        )
    except ValidationFailure as exc:
        raise HTTPException(status_code=400, detail=f"proposal verification failed: {exc}")

    audit_req = AuditRequest(
        pipeline_id=proposal.pipeline_id,
        caller_sub=user.sub,
        caller_role=user.role.value,
        triage=triage,
        analyst_proposal=proposal,
        verification={
            "coverage": stored.get("coverage"),
            "ownership": stored.get("ownership"),
            "identity": stored.get("identity"),
            "amount": stored.get("amount"),
        },
    )
    outcome = await app_state.auditor.run(audit_req)

    if outcome.status != "success":
        return AuditResponse(status="manual_review", steps_used=outcome.steps_used, reason=outcome.reason)

    decision_dump = outcome.decision.model_dump(mode="json") if outcome.decision else None
    signed_dump = outcome.signed_proposal.model_dump(mode="json") if outcome.signed_proposal else None
    decision_id = str(outcome.signed_proposal.proposal_id) if outcome.signed_proposal else None

    await app_state.hitl.put_decision(
        decision_id=decision_id,
        owner_sub=user.sub,
        payload={
            "owner_sub": user.sub,
            "submitter_sub": stored["owner_sub"],
            "claimant_sub": stored.get("claimant_sub"),
            "claim_id": stored.get("claim_id"),
            "analyst_proposal": stored["proposal"],
            "analyst_triage": stored["triage"],
            "auditor_proposal": signed_dump,
            "auditor_decision": decision_dump,
        },
    )
    return AuditResponse(
        status="success",
        steps_used=outcome.steps_used,
        decision_id=decision_id,
        decision=decision_dump,
        signed_proposal=signed_dump,
    )


@router.get("/pending", response_model=PendingResponse)
async def pending(
    request: Request,
    user: UserPrincipal = Depends(require_role(Role.ANALYST)),
) -> PendingResponse:
    hitl: HITLStore = request.app.state.hitl
    proposal_ids = await hitl.list_proposals_for(user.sub)
    decisions = await hitl.list_decisions_for(user.sub)

    summaries: list[PendingProposalSummary] = []
    for pid in proposal_ids:
        try:
            stored = await hitl.get_proposal(pid, caller_sub=user.sub, caller_role=user.role.value)
        except (NotFoundError, ForbiddenError):
            continue  # expired or not ours — skip silently
        triage = stored.get("triage") or {}
        proposal = stored.get("proposal") or {}
        summaries.append(PendingProposalSummary(
            proposal_id=pid,
            pipeline_id=stored.get("pipeline_id"),
            claimant_sub=stored.get("claimant_sub"),
            policy_id=triage.get("policy_id"),
            reported_damage_amount=triage.get("reported_damage_amount"),
            severity=triage.get("severity"),
            summary=triage.get("summary"),
            created_at=proposal.get("issued_at"),
            coverage=stored.get("coverage"),
            ownership=stored.get("ownership"),
            identity=stored.get("identity"),
            amount=stored.get("amount"),
        ))
    return PendingResponse(proposals=summaries, decisions=decisions)


@router.get("/claims", response_model=ClaimsListResponse)
async def list_claims(
    request: Request,
    user: UserPrincipal = Depends(require_role(Role.ANALYST)),
) -> ClaimsListResponse:
    """Persisted claims from Postgres. ADMIN sees all; ANALYST sees only its own."""
    pool = request.app.state.pool
    result = await pool.call(
        "claims", "list_claims_by_submitter",
        {"caller_sub": user.sub, "caller_role": user.role.value, "limit": 200},
    )
    return ClaimsListResponse(claims=result.get("claims", []))


@router.get("/proposal/{proposal_id}", response_model=ProposalDetailResponse)
async def proposal_detail(
    proposal_id: UUID,
    request: Request,
    user: UserPrincipal = Depends(require_role(Role.ANALYST)),
) -> ProposalDetailResponse:
    """Full triage for the HITL screen.

    Lookup order: (1) the live signed proposal in Redis (by proposal_id), then
    (2) the durable claim in Postgres (by claim id), so the screen survives the
    Redis TTL / a refresh. Postgres mode is read-only (no signed proposal to audit).
    """
    hitl: HITLStore = request.app.state.hitl
    ref = str(proposal_id)

    # 1. Live proposal in Redis (still auditable/confirmable).
    try:
        stored = await hitl.get_proposal(ref, caller_sub=user.sub, caller_role=user.role.value)
        return ProposalDetailResponse(
            proposal_id=ref,
            audit_proposal_id=ref,
            source="redis",
            pipeline_id=stored.get("pipeline_id"),
            claimant_sub=stored.get("claimant_sub"),
            coverage=stored.get("coverage"),
            ownership=stored.get("ownership"),
            identity=stored.get("identity"),
            amount=stored.get("amount"),
            auditor=stored.get("auditor"),
            audit_decision_id=stored.get("audit_decision_id"),
            triage=stored.get("triage"),
            proposal=stored.get("proposal"),
        )
    except ForbiddenError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except NotFoundError:
        pass  # expired or not a proposal id → try the durable store

    # 2. Durable claim in Postgres (survives TTL/refresh; read-only).
    try:
        res = await request.app.state.pool.call(
            "claims", "get_claim_by_id",
            {"claim_id": ref, "caller_sub": user.sub, "caller_role": user.role.value},
        )
        claim = res.get("claim") or {}
    except (NotFoundError, ForbiddenError):
        raise HTTPException(status_code=404, detail="proposal not found or expired")
    if not claim:
        raise HTTPException(status_code=404, detail="proposal not found or expired")

    meta = claim.get("decision_metadata") or {}
    triage = {
        "policy_id": claim.get("policy_id"),
        "pdf_sha256": claim.get("pdf_sha256"),
        "reported_damage_amount": claim.get("reported_damage_amount"),
        "severity": claim.get("severity"),
        "summary": claim.get("ai_summary"),
        "evidence_quotes": claim.get("evidence_quotes") or [],
    }
    return ProposalDetailResponse(
        proposal_id=ref,
        audit_proposal_id=meta.get("proposal_id"),  # may be None if its Redis TTL passed
        source="postgres",
        pipeline_id=meta.get("pipeline_id"),
        claimant_sub=claim.get("claimant_sub"),
        coverage=meta.get("coverage"),
        ownership=meta.get("ownership"),
        identity=meta.get("identity"),
        amount=meta.get("amount"),
        auditor=meta.get("auditor"),
        audit_decision_id=None,  # the signed decision shares the proposal TTL — gone in Postgres mode
        triage=triage,
        proposal={},
    )


@router.post("/confirm", response_model=ConfirmResponse)
async def confirm(
    req: ConfirmRequest,
    request: Request,
    user: UserPrincipal = Depends(require_role(Role.AUDITOR)),
) -> ConfirmResponse:
    async with get_tracer().trace(
        "claim.confirm",
        decision_id=str(req.decision_id),
        approval=req.approval,
        approver_sub=user.sub,
        approver_role=user.role.value,
    ) as root:
        response = await _confirm(req, request, user, root)
        root.set_attributes(outcome=response.status)
        root.score("human_decision", req.approval)
        root.score("persisted", response.status == "persisted")
        if response.status == "refused":
            root.set_status(False, message=response.reason or "refused")
        return response


async def _confirm(
    req: ConfirmRequest, request: Request, user: UserPrincipal, root: Span
) -> ConfirmResponse:
    app_state = request.app.state
    try:
        stored = await app_state.hitl.get_decision(
            str(req.decision_id), caller_sub=user.sub, caller_role=user.role.value
        )
    except (NotFoundError, ForbiddenError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, NotFoundError) else 403, detail=str(exc))

    pipeline_id = (stored.get("analyst_triage") or {}).get("pipeline_id")
    if pipeline_id:
        root.set_session(str(pipeline_id))
        root.set_attributes(pipeline_id=str(pipeline_id))

    if req.approval == "reject":
        await app_state.hitl.mark_consumed(str(req.decision_id))
        # Reflect the human rejection on the durable claim row (created at intake).
        claim_id = stored.get("claim_id")
        if claim_id:
            try:
                await dispatch(
                    app_state.pool, "claims__update_claim_status",
                    {
                        "claim_id": claim_id,
                        "new_status": "REJECTED",
                        "decision_metadata": {
                            "rejected_by_sub": user.sub,
                            "rejected_by_role": user.role.value,
                            "outcome": "rejected_by_human",
                        },
                        "caller_sub": user.sub,
                        "caller_role": user.role.value,
                    },
                    allow_writes=True,
                )
            except Exception as exc:  # noqa: BLE001 — status update is best-effort
                logger.warning("reject status update failed | claim_id=%s err=%s", claim_id, exc)
        return ConfirmResponse(status="rejected_by_human", reason="human rejected")

    if not await app_state.hitl.mark_consumed(str(req.decision_id)):
        return ConfirmResponse(status="refused", reason="decision already consumed")

    analyst_proposal = from_stored(ClaimProposal, stored["analyst_proposal"])
    analyst_triage = from_stored(TriageReport, stored["analyst_triage"])
    auditor_proposal = from_stored(ClaimProposal, stored["auditor_proposal"])
    auditor_decision = from_stored(ClaimDecision, stored["auditor_decision"])

    approval = ApprovalContext(
        approver_sub=user.sub,
        approver_role=user.role.value,
        submitter_sub=stored["submitter_sub"],
        claimant_sub=stored.get("claimant_sub"),
        claim_id=stored.get("claim_id"),
    )
    try:
        result = await app_state.mutation.apply(
            approval=approval,
            analyst_proposal=analyst_proposal,
            analyst_triage=analyst_triage,
            auditor_proposal=auditor_proposal,
            auditor_decision=auditor_decision,
        )
    except ForbiddenError as exc:
        return ConfirmResponse(status="refused", reason=str(exc))

    if not result.persisted:
        return ConfirmResponse(status="refused", reason=result.reason)
    return ConfirmResponse(status="persisted", claim=result.claim)
