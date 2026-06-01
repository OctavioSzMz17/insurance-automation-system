"""Agent pipeline routes: analyze → audit → pending → confirm.

All endpoints require a valid JWT. caller_sub and caller_role are
extracted from the token — not accepted in the request body.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from agents.analyst import Analyst, RunContext
from agents.auditor import AuditRequest, Auditor
from agents.mutation_handler import ApprovalContext, MutationHandler
from auth import Role, UserPrincipal, require_role
from core import proposal as proposal_mod
from core.hitl import HITLStore
from core.tools import dispatch
from packages.common.exceptions import ForbiddenError, NotFoundError
from packages.common.schemas.claim import ClaimDecision, ClaimProposal
from packages.common.schemas.triage import TriageReport

logger = logging.getLogger(__name__)
router = APIRouter(tags=["agents"])


def _evaluate_coverage(policy: dict[str, Any]) -> dict[str, Any]:
    """Deterministic policy-validity check (Rule 4: hard facts are not left to the LLM).

    A policy is in force when its status is ACTIVE *and* today falls within the
    [effective_from, effective_to] window. ISO YYYY-MM-DD strings compare lexically.
    """
    status = str(policy.get("status") or "").upper()
    ef = str(policy.get("effective_from") or "")[:10]
    et = str(policy.get("effective_to") or "")[:10]
    today = date.today().isoformat()

    active = status == "ACTIVE"
    in_window = bool(ef and et and ef <= today <= et)
    in_force = active and in_window

    if in_force:
        reason = "Póliza vigente."
    elif not active:
        reason = f"Estado de póliza: {status or 'desconocido'}."
    elif et and today > et:
        reason = f"La vigencia terminó el {et}."
    elif ef and today < ef:
        reason = f"La vigencia inicia el {ef}."
    else:
        reason = "Vigencia no determinable."

    return {
        "in_force": in_force,
        "status": status,
        "effective_from": ef or None,
        "effective_to": et or None,
        "reason": reason,
    }


def _norm_text(s: str) -> str:
    """Lowercase + strip accents + collapse whitespace, for robust name matching."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")
    return " ".join(s.lower().split())


def _evaluate_identity(claimant_name: str | None, pdf_text: str) -> dict[str, Any]:
    """Deterministic anti-impersonation check (Rule 4).

    Confirms the claim document actually names the CLIENT the operator filed it
    for. Catches "filed on behalf of Ana but the PDF belongs to Carlos".
    A match is the full normalized name as a substring, OR every name token
    (len>=3) present as a whole word.
    """
    name = _norm_text(claimant_name or "")
    text = _norm_text(pdf_text or "")
    if not name:
        return {"match": None, "claimant_name": claimant_name,
                "reason": "El cliente seleccionado no tiene nombre registrado."}

    tokens = [t for t in name.split() if len(t) >= 3]
    tokens_present = bool(tokens) and all(re.search(rf"\b{re.escape(t)}\b", text) for t in tokens)
    match = (name in text) or tokens_present

    reason = (
        "El documento menciona al asegurado seleccionado."
        if match else
        f"El documento NO menciona a «{claimant_name}» — posible suplantación de identidad."
    )
    return {"match": match, "claimant_name": claimant_name, "reason": reason}


def _evaluate_amount(reported_amount: Any, pdf_text: str) -> dict[str, Any]:
    """Deterministic anti-hallucination check on the monetary amount (Rule 4).

    An LLM may invent a damage figure not present in the document. This confirms
    the reported amount actually appears in the PDF text. $0 (no amount claimed)
    is treated as supported — it is the honest 'no figure stated' outcome.
    """
    if reported_amount in (None, ""):
        return {"supported": None, "amount": reported_amount, "reason": "Sin monto reportado."}
    try:
        val = round(float(reported_amount), 2)
    except (TypeError, ValueError):
        return {"supported": None, "amount": reported_amount, "reason": "Monto no numérico."}
    if val == 0:
        return {"supported": True, "amount": reported_amount, "reason": "Sin monto reclamado ($0)."}

    # Collect every monetary-looking number present in the document.
    pdf_values: set[float] = set()
    for tok in re.findall(r"\$?\s*(\d[\d.,]*)", pdf_text or ""):
        cleaned = tok.replace(",", "").rstrip(".")
        try:
            pdf_values.add(round(float(cleaned), 2))
        except ValueError:
            continue

    supported = val in pdf_values
    reason = (
        "El monto reportado aparece en el documento."
        if supported else
        f"El monto reportado (${reported_amount}) NO aparece en el documento — posible valor inventado por la IA."
    )
    return {"supported": supported, "amount": reported_amount, "reason": reason}


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


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(
    req: AnalyzeRequest,
    request: Request,
    user: UserPrincipal = Depends(require_role(Role.ANALYST)),
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

    # ── Deterministic coverage check on the resolved policy (Rule 4) ──────────
    coverage: dict[str, Any] | None = None
    policy_id = (triage_dump or {}).get("policy_id")
    if policy_id:
        try:
            pol_res = await app_state.pool.call(
                "policies", "get_policy_by_id",
                {"policy_id": policy_id, "caller_sub": user.sub, "caller_role": user.role.value},
            )
            coverage = _evaluate_coverage(pol_res.get("policy") or {})
        except Exception as exc:  # noqa: BLE001 — coverage is best-effort, never blocks triage
            logger.warning("coverage check failed | policy_id=%s err=%s", policy_id, exc)

    # ── Fetch the PDF text once for the checks that read the document. ─────────
    pdf_text = ""
    try:
        doc = await app_state.pool.call(
            "documents", "extract_text",
            {"pipeline_id": str(req.pipeline_id), "caller_sub": user.sub, "caller_role": user.role.value},
        )
        pdf_text = doc.get("text") or ""
    except Exception as exc:  # noqa: BLE001 — best-effort, never blocks triage
        logger.warning("pdf text fetch failed | err=%s", exc)

    # ── Identity check: does the PDF actually name the selected claimant?
    #    Catches filing a claim on behalf of the wrong client (Rule 4). ──
    identity: dict[str, Any] | None = None
    if req.claimant_sub:
        try:
            usr = await app_state.pool.call(
                "users", "get_user_by_email",
                {"email": req.claimant_sub, "caller_sub": user.sub, "caller_role": user.role.value},
            )
            identity = _evaluate_identity((usr.get("user") or {}).get("full_name"), pdf_text)
        except Exception as exc:  # noqa: BLE001 — best-effort, never blocks triage
            logger.warning("identity check failed | claimant=%s err=%s", req.claimant_sub, exc)

    # ── Amount check: is the reported figure actually in the PDF? Catches a
    #    hallucinated monetary value (Rule 4). ──
    amount: dict[str, Any] | None = None
    if triage_dump:
        amount = _evaluate_amount(triage_dump.get("reported_damage_amount"), pdf_text)

    # ── Deterministic verification report → review flags (shown in HITL and fed
    #    to the auditor as ground truth). ──
    review_flags: list[str] = []
    if coverage and not coverage.get("in_force"):
        review_flags.append(
            f"Póliza no vigente: {coverage.get('reason')} "
            f"(vigencia {coverage.get('effective_from')} → {coverage.get('effective_to')})."
        )
    if identity and identity.get("match") is False:
        review_flags.append(f"Posible suplantación: {identity.get('reason')}")
    if amount and amount.get("supported") is False:
        review_flags.append(f"Monto no respaldado: {amount.get('reason')}")

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
            verification={"coverage": coverage, "identity": identity, "amount": amount},
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
    app_state = request.app.state
    try:
        stored = await app_state.hitl.get_proposal(
            str(req.proposal_id), caller_sub=user.sub, caller_role=user.role.value
        )
    except (NotFoundError, ForbiddenError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, NotFoundError) else 403, detail=str(exc))

    proposal = ClaimProposal(**stored["proposal"])
    triage = TriageReport(**stored["triage"])

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
    app_state = request.app.state
    try:
        stored = await app_state.hitl.get_decision(
            str(req.decision_id), caller_sub=user.sub, caller_role=user.role.value
        )
    except (NotFoundError, ForbiddenError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, NotFoundError) else 403, detail=str(exc))

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

    analyst_proposal = ClaimProposal(**stored["analyst_proposal"])
    analyst_triage = TriageReport(**stored["analyst_triage"])
    auditor_proposal = ClaimProposal(**stored["auditor_proposal"])
    auditor_decision = ClaimDecision(**stored["auditor_decision"])

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
