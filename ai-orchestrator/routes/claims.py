"""Claims endpoints — Phase C (C1.1 → C1.4).

Endpoints:
    POST /claims/submit             — analyst+ — full agent pipeline
    GET  /claims/{claim_id}         — analyst+ — read claim row
    POST /claims/{claim_id}/approve — auditor+ — manual override to APPROVED

All endpoints are gated by JWT bearer auth + role check (Rule 7).

Rules applied:
    Rule 1  — Pydantic schemas imported from packages/common/
    Rule 4  — `actor` for approve comes from JWT principal (no impersonation)
    Rule 7  — Every endpoint Depends(require_role(...))
    Rule 9  — Structured logging on every request
    Rule 11 — Type hints throughout
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)

_ORCH_ROOT     = Path(__file__).resolve().parents[1]
_PROJECT_ROOT  = _ORCH_ROOT.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_ORCH_ROOT))

from packages.common.schemas.api import (  # noqa: E402
    ClaimSubmitRequest,
    ClaimSubmitResponse,
    ClaimStatusResponse,
    ClaimListResponse,
    ClaimApproveRequest,
    ClaimRejectRequest,
)

from auth                  import Role, UserPrincipal, require_role  # noqa: E402
from routes.mcp_router      import MCPRouter, get_mcp_router          # noqa: E402
from validation             import (                                   # noqa: E402
    validate_approval_input,
    record_approval_action,
    http_status_for,
)

logger = logging.getLogger(__name__)
router  = APIRouter(prefix="/claims", tags=["claims"])


# ── POST /claims/submit ──────────────────────────────────────────

@router.post(
    "/submit",
    response_model=ClaimSubmitResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit a new claim PDF for processing",
    description=(
        "Runs the full analyst → auditor pipeline against the PDF "
        "at `pdf_path` and returns the structured decision. "
        "Requires `analyst` role or higher."
    ),
)
async def submit_claim(
    body:       ClaimSubmitRequest,
    request:    Request,
    user:       UserPrincipal = Depends(require_role(Role.ANALYST)),
    mcp_router: MCPRouter     = Depends(get_mcp_router),
) -> ClaimSubmitResponse:
    """Run the full agent pipeline for a single claim PDF."""
    request_id = str(uuid.uuid4())
    logger.info(
        f"POST /claims/submit | request_id={request_id} actor={user.sub} "
        f"role={user.role.value} pdf_path={body.pdf_path}"
    )

    agent_router = getattr(request.app.state, "agent_router", None)
    if agent_router is None:
        logger.error(f"AgentRouter not initialised | request_id={request_id}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent router not ready — try again shortly.",
        )

    result = await agent_router.process_claim(body.pdf_path)

    recommendation: str | None = None
    persisted_id: int | None = None
    claim_status_persisted: str | None = None
    summary: str | None = None
    if result.claim_decision:
        recommendation = result.claim_decision.get("decision")
        persisted_id   = result.claim_decision.get("persisted_claim_id")
    if result.triage_report:
        summary = result.triage_report.get("summary")

    # After C1.5, the persisted row should be AWAITING_INPUT until a human
    # confirms via /approve or /reject. Verify by going through the MCP router.
    if persisted_id is not None:
        try:
            persisted = await mcp_router.call(
                "claims", "get_claim_by_id",
                caller_role=user.role,
                claim_id=persisted_id,
            )
            claim_status_persisted = persisted.get("claim_status")
        except Exception as e:
            logger.warning(
                f"POST /claims/submit | could not re-read persisted claim "
                f"{persisted_id}: {e}"
            )

    logger.info(
        f"POST /claims/submit done | request_id={request_id} actor={user.sub} "
        f"status={result.pipeline_status} recommended={recommendation} "
        f"claim_status={claim_status_persisted} "
        f"persisted_claim_id={persisted_id} latency_ms={result.total_latency_ms}"
    )

    return ClaimSubmitResponse(
        request_id=request_id,
        pipeline_status=result.pipeline_status,
        recommended_decision=recommendation,
        claim_status=claim_status_persisted,
        persisted_claim_id=persisted_id,
        summary=summary,
        triage_report=result.triage_report,
        claim_decision=result.claim_decision,
        analyst_retries=result.analyst_retries,
        auditor_retries=result.auditor_retries,
        analyst_latency_ms=result.analyst_latency_ms,
        auditor_latency_ms=result.auditor_latency_ms,
        total_latency_ms=result.total_latency_ms,
        error_message=result.error_message,
    )


# ── POST /claims/submit-upload (multipart) ───────────────────────

# Shared volume for uploaded PDFs (inside the orchestrator container).
# In production this is a PVC mounted by both orchestrator + documents MCP.
_UPLOAD_DIR = Path(os.getenv("CLAIM_UPLOAD_DIR", "/tmp/claim-uploads"))
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB cap (matches PDF MCP limit)


def _pdf_path_for_claim(claim_id: int) -> Path:
    """Canonical on-disk location for a claim's original PDF (re-analysis)."""
    return _UPLOAD_DIR / f"claim-{claim_id}.pdf"


@router.post(
    "/submit-upload",
    response_model=ClaimSubmitResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit a new claim PDF via multipart upload",
    description=(
        "Accepts a PDF file as `multipart/form-data` (browser-friendly), "
        "stages it in the orchestrator's upload volume, then runs the "
        "full analyst → auditor pipeline. Requires `analyst` role+."
    ),
    responses={
        400: {"description": "Missing or invalid PDF file"},
        401: {"description": "Missing or invalid token"},
        403: {"description": "Insufficient role"},
        413: {"description": "File exceeds size limit"},
        503: {"description": "Agent router not ready"},
    },
)
async def submit_claim_upload(
    request:    Request,
    pdf:        UploadFile = File(..., description="Claim report PDF"),
    user:       UserPrincipal = Depends(require_role(Role.ANALYST)),
    mcp_router: MCPRouter     = Depends(get_mcp_router),
) -> ClaimSubmitResponse:
    """Upload a PDF and run the full agent pipeline against it."""
    request_id = str(uuid.uuid4())
    logger.info(
        f"POST /claims/submit-upload | request_id={request_id} actor={user.sub} "
        f"role={user.role.value} filename={pdf.filename}"
    )

    # ── Validate ─────────────────────────────────────────────
    if not pdf.filename or not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A .pdf file is required",
        )

    # ── Stage the upload on disk with a stable, sanitised name ──
    safe_name = f"{request_id}.pdf"
    target = _UPLOAD_DIR / safe_name
    try:
        bytes_written = 0
        with target.open("wb") as out:
            while True:
                chunk = await pdf.read(1024 * 1024)  # 1 MB chunks
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > _MAX_UPLOAD_BYTES:
                    out.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"PDF exceeds {_MAX_UPLOAD_BYTES // (1024*1024)} MB limit",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"POST /claims/submit-upload | stage failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to stage uploaded file",
        )

    logger.info(
        f"POST /claims/submit-upload | request_id={request_id} "
        f"staged bytes={bytes_written} path={target}"
    )

    # ── Delegate to the existing pipeline path ───────────────
    agent_router = getattr(request.app.state, "agent_router", None)
    if agent_router is None:
        target.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent router not ready — try again shortly.",
        )

    try:
        result = await agent_router.process_claim(str(target))
    finally:
        # Don't delete the staged file — useful for forensic replay and
        # for the /reanalyze endpoint that re-runs the agents against it.
        pass

    recommendation: str | None = None
    persisted_id: int | None = None
    claim_status_persisted: str | None = None
    summary: str | None = None
    if result.claim_decision:
        recommendation = result.claim_decision.get("decision")
        persisted_id   = result.claim_decision.get("persisted_claim_id")
    if result.triage_report:
        summary = result.triage_report.get("summary")

    if persisted_id is not None:
        # Rename the staged PDF so it can be looked up by claim_id later
        # (re-analysis flow). Naming convention is enforced in one place.
        try:
            stable = _pdf_path_for_claim(persisted_id)
            if target.exists() and target != stable:
                target.replace(stable)
                logger.info(
                    f"POST /claims/submit-upload | PDF renamed for replay | "
                    f"claim_id={persisted_id} path={stable}"
                )
        except Exception as e:
            logger.warning(
                f"POST /claims/submit-upload | could not rename PDF for "
                f"claim {persisted_id}: {e}"
            )

        try:
            persisted = await mcp_router.call(
                "claims", "get_claim_by_id",
                caller_role=user.role,
                claim_id=persisted_id,
            )
            claim_status_persisted = persisted.get("claim_status")
        except Exception as e:
            logger.warning(
                f"POST /claims/submit-upload | could not re-read persisted "
                f"claim {persisted_id}: {e}"
            )

    return ClaimSubmitResponse(
        request_id=request_id,
        pipeline_status=result.pipeline_status,
        recommended_decision=recommendation,
        claim_status=claim_status_persisted,
        persisted_claim_id=persisted_id,
        summary=summary,
        triage_report=result.triage_report,
        claim_decision=result.claim_decision,
        analyst_retries=result.analyst_retries,
        auditor_retries=result.auditor_retries,
        analyst_latency_ms=result.analyst_latency_ms,
        auditor_latency_ms=result.auditor_latency_ms,
        total_latency_ms=result.total_latency_ms,
        error_message=result.error_message,
    )


# ── GET /claims (list) ───────────────────────────────────────────

@router.get(
    "",
    response_model=ClaimListResponse,
    status_code=status.HTTP_200_OK,
    summary="List persisted claims (review queue)",
    description=(
        "Returns a paginated list of claim rows ordered most-recent-first. "
        "Optional `status` filter narrows by claim_status (e.g. "
        "`MANUAL_REVIEW`). Requires `analyst` role+."
    ),
    responses={
        400: {"description": "Invalid status / pagination params"},
        401: {"description": "Missing or invalid token"},
        403: {"description": "Insufficient role"},
        500: {"description": "Database error"},
    },
)
async def list_claims_endpoint(
    status_filter: Optional[str] = Query(
        None, alias="status",
        description="Filter by claim_status (e.g. MANUAL_REVIEW).",
    ),
    limit:  int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user:       UserPrincipal = Depends(require_role(Role.ANALYST)),
    mcp_router: MCPRouter     = Depends(get_mcp_router),
) -> ClaimListResponse:
    """Return the review queue."""
    logger.info(
        f"GET /claims | actor={user.sub} role={user.role.value} "
        f"status={status_filter} limit={limit} offset={offset}"
    )

    try:
        rows = await mcp_router.call(
            "claims", "list_claims",
            caller_role=user.role,
            status=status_filter,
            limit=limit,
            offset=offset,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except RuntimeError as e:
        logger.error(f"GET /claims DB error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error listing claims",
        )

    return ClaimListResponse(
        claims=[ClaimStatusResponse(**r) for r in rows],
        total=len(rows),
        limit=limit,
        offset=offset,
    )


# ── GET /claims/{claim_id} ───────────────────────────────────────

@router.get(
    "/{claim_id}",
    response_model=ClaimStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Get the current status of a persisted claim",
    description=(
        "Fetches a single claim by its integer primary key. "
        "Read-only — requires `analyst` role or higher."
    ),
    responses={
        401: {"description": "Missing or invalid token"},
        403: {"description": "Insufficient role"},
        404: {"description": "No claim exists with the given claim_id"},
        500: {"description": "Database query failure"},
    },
)
async def get_claim_status(
    claim_id:   int,
    user:       UserPrincipal = Depends(require_role(Role.ANALYST)),
    mcp_router: MCPRouter     = Depends(get_mcp_router),
) -> ClaimStatusResponse:
    """Return the current persisted state of a single claim."""
    logger.info(f"GET /claims/{claim_id} | actor={user.sub} role={user.role.value}")

    try:
        row = await mcp_router.call(
            "claims", "get_claim_by_id",
            caller_role=user.role,
            claim_id=claim_id,
        )
    except ValueError as e:
        logger.warning(f"GET /claims/{claim_id} not found: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Claim {claim_id} not found",
        )
    except RuntimeError as e:
        logger.error(f"GET /claims/{claim_id} DB error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error retrieving claim",
        )

    return ClaimStatusResponse(**row)


# ── Re-analysis (shared logic) ───────────────────────────────────

async def _verify_claim_exists(
    claim_id: int, caller_role: Role, mcp_router: MCPRouter,
) -> None:
    """Raise 404 if no claim row exists with this id."""
    try:
        await mcp_router.call(
            "claims", "get_claim_by_id",
            caller_role=caller_role, claim_id=claim_id,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Claim {claim_id} not found",
        )


async def _run_reanalysis(
    *,
    claim_id:   int,
    pdf_path:   Path,
    actor:      str,
    caller_role: Role,
    request:    Request,
    mcp_router: MCPRouter,
) -> ClaimStatusResponse:
    """Shared body: run agents in advisory mode + append reasoning.

    Assumes `pdf_path` already points at a real file. Both /reanalyze
    and /reanalyze-upload funnel into this so the audit-trail format
    stays identical regardless of how the PDF got staged.
    """
    from datetime import datetime, timezone
    import json as _json

    agent_router = getattr(request.app.state, "agent_router", None)
    if agent_router is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent router not ready — try again shortly.",
        )

    result = await agent_router.process_claim(str(pdf_path), persist=False)

    if result.pipeline_status != "SUCCESS":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"Re-analysis pipeline returned {result.pipeline_status}: "
                f"{result.error_message or 'no details'}"
            ),
        )

    decision = "UPDATED"
    if result.claim_decision:
        decision = result.claim_decision.get("decision") or "UPDATED"
    summary = (result.triage_report or {}).get("summary") or ""
    notes_payload: dict[str, object] = {
        "decision": decision,
        "summary": summary,
        "flags": (result.triage_report or {}).get("flags") or [],
        "auditor_notes": (result.claim_decision or {}).get("rationale")
                         or (result.claim_decision or {}).get("notes")
                         or "",
    }
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    block = (
        f"[RE-ANALYSIS — {ts} by {actor}]\n"
        f"Recommendation: {decision}. "
        f"{notes_payload['auditor_notes'] or summary or 'No additional notes.'}\n"
        f"Raw: {_json.dumps(notes_payload, ensure_ascii=False)}"
    )

    try:
        updated = await mcp_router.call(
            "claims", "append_reasoning",
            caller_role=caller_role,
            claim_id=claim_id,
            text_to_append=block,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e),
        )
    except Exception as e:
        logger.error(
            f"reanalyze append_reasoning failed | claim={claim_id} err={e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Re-analysis succeeded but could not be saved.",
        )

    logger.info(
        f"reanalyze done | claim={claim_id} actor={actor} recommended={decision}"
    )
    return ClaimStatusResponse(**updated)


# ── POST /claims/{claim_id}/reanalyze ────────────────────────────

@router.post(
    "/{claim_id}/reanalyze",
    response_model=ClaimStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Re-run Lía against the stored original PDF",
    description=(
        "Looks up the original PDF for this claim on the orchestrator "
        "volume, runs the analyst → auditor pipeline in advisory mode "
        "(no new DB row), and appends the fresh recommendation. "
        "Returns 409 when the PDF is no longer on disk — callers should "
        "retry via `/reanalyze-upload`. Requires `auditor` role."
    ),
    responses={
        401: {"description": "Missing or invalid token"},
        403: {"description": "Insufficient role"},
        404: {"description": "Claim not found"},
        409: {"description": "Original PDF missing — upload it via /reanalyze-upload"},
        502: {"description": "Agent pipeline failed"},
        503: {"description": "Agent router not ready"},
    },
)
async def reanalyze_claim(
    claim_id:   int,
    request:    Request,
    user:       UserPrincipal = Depends(require_role(Role.AUDITOR)),
    mcp_router: MCPRouter     = Depends(get_mcp_router),
) -> ClaimStatusResponse:
    logger.info(
        f"POST /claims/{claim_id}/reanalyze | actor={user.sub} "
        f"role={user.role.value}"
    )
    await _verify_claim_exists(claim_id, user.role, mcp_router)

    pdf = _pdf_path_for_claim(claim_id)
    if not pdf.exists():
        logger.info(
            f"POST /claims/{claim_id}/reanalyze | PDF missing — 409 returned"
        )
        # 409 Conflict: the claim is fine, but the request can't proceed
        # in its current form (no source document). The structured detail
        # lets the UI flip cleanly into upload mode.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "pdf_missing",
                "message": (
                    f"Original PDF for claim {claim_id} is not on disk. "
                    f"Please re-upload it to continue."
                ),
                "claim_id": claim_id,
            },
        )

    return await _run_reanalysis(
        claim_id=claim_id, pdf_path=pdf,
        actor=user.sub, caller_role=user.role,
        request=request, mcp_router=mcp_router,
    )


# ── POST /claims/{claim_id}/reanalyze-upload ─────────────────────

@router.post(
    "/{claim_id}/reanalyze-upload",
    response_model=ClaimStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload a fresh PDF and re-run Lía against it",
    description=(
        "Fallback for /reanalyze when the orchestrator no longer has "
        "the original PDF on disk (e.g. after a redeploy without a "
        "persistent volume). The uploaded file is stored as the "
        "claim's canonical PDF so subsequent re-analyses don't require "
        "re-uploading. Requires `auditor` role."
    ),
    responses={
        400: {"description": "Missing or invalid PDF"},
        401: {"description": "Missing or invalid token"},
        403: {"description": "Insufficient role"},
        404: {"description": "Claim not found"},
        413: {"description": "PDF exceeds size limit"},
    },
)
async def reanalyze_claim_upload(
    claim_id:   int,
    request:    Request,
    pdf:        UploadFile = File(..., description="Claim report PDF"),
    user:       UserPrincipal = Depends(require_role(Role.AUDITOR)),
    mcp_router: MCPRouter     = Depends(get_mcp_router),
) -> ClaimStatusResponse:
    logger.info(
        f"POST /claims/{claim_id}/reanalyze-upload | actor={user.sub} "
        f"role={user.role.value} filename={pdf.filename}"
    )
    await _verify_claim_exists(claim_id, user.role, mcp_router)

    if not pdf.filename or not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A .pdf file is required",
        )

    # Stream directly into the canonical location for this claim. Any
    # previous file (e.g. partial / corrupted) is replaced atomically.
    target = _pdf_path_for_claim(claim_id)
    try:
        bytes_written = 0
        with target.open("wb") as out:
            while True:
                chunk = await pdf.read(1024 * 1024)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > _MAX_UPLOAD_BYTES:
                    out.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=(
                            f"PDF exceeds "
                            f"{_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit"
                        ),
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"POST /claims/{claim_id}/reanalyze-upload stage failed: {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to stage uploaded file",
        )

    logger.info(
        f"POST /claims/{claim_id}/reanalyze-upload | bytes={bytes_written} "
        f"stored={target}"
    )

    return await _run_reanalysis(
        claim_id=claim_id, pdf_path=target,
        actor=user.sub, caller_role=user.role,
        request=request, mcp_router=mcp_router,
    )


# ── POST /claims/{claim_id}/approve ──────────────────────────────

@router.post(
    "/{claim_id}/approve",
    response_model=ClaimStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Manually approve a claim",
    description=(
        "Override a claim currently in MANUAL_REVIEW or AWAITING_INPUT and "
        "move it to APPROVED. Requires `auditor` role or higher. The "
        "approver identity is taken from the JWT `sub` claim — never "
        "from the request body — so impersonation is impossible."
    ),
    responses={
        401: {"description": "Missing or invalid token"},
        403: {"description": "Insufficient role"},
        404: {"description": "No claim exists with the given claim_id"},
        409: {"description": "Claim is in a state that can't be approved"},
        500: {"description": "Database error"},
    },
)
async def approve_claim(
    claim_id:   int,
    body:       ClaimApproveRequest,
    user:       UserPrincipal = Depends(require_role(Role.AUDITOR)),
    mcp_router: MCPRouter     = Depends(get_mcp_router),
) -> ClaimStatusResponse:
    """Move a MANUAL_REVIEW / AWAITING_INPUT claim to APPROVED."""
    return await _decide_claim(
        claim_id=claim_id, action="approve", new_status="APPROVED",
        reason=body.reason, user=user, mcp_router=mcp_router,
    )


# ── POST /claims/{claim_id}/reject ───────────────────────────────

@router.post(
    "/{claim_id}/reject",
    response_model=ClaimStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Manually reject a claim",
    description=(
        "Symmetric to /approve. Override a claim currently in MANUAL_REVIEW "
        "or AWAITING_INPUT and move it to REJECTED. Requires `auditor` role "
        "or higher. Rejector identity comes from the JWT `sub` claim."
    ),
    responses={
        401: {"description": "Missing or invalid token"},
        403: {"description": "Insufficient role"},
        404: {"description": "No claim exists with the given claim_id"},
        409: {"description": "Claim is in a state that can't be rejected"},
        500: {"description": "Database error"},
    },
)
async def reject_claim(
    claim_id:   int,
    body:       ClaimRejectRequest,
    user:       UserPrincipal = Depends(require_role(Role.AUDITOR)),
    mcp_router: MCPRouter     = Depends(get_mcp_router),
) -> ClaimStatusResponse:
    """Move a MANUAL_REVIEW / AWAITING_INPUT claim to REJECTED."""
    return await _decide_claim(
        claim_id=claim_id, action="reject", new_status="REJECTED",
        reason=body.reason, user=user, mcp_router=mcp_router,
    )


# ── Shared helper for /approve and /reject (Rule 6 + C2.2 guards) ─

async def _decide_claim(
    *,
    claim_id:   int,
    action:     str,                  # "approve" | "reject"
    new_status: str,                  # "APPROVED" | "REJECTED"
    reason:     str,
    user:       UserPrincipal,
    mcp_router: MCPRouter,
) -> ClaimStatusResponse:
    """Run C2.2 input guards, then route to MCP. Used by approve + reject.

    Flow:
        1. validate_approval_input → reason check + rate limit + idempotency
        2. If idempotent replay → return cached result (no DB hit)
        3. If violations → 422 (or 429 for rate limit)
        4. Otherwise call MCP → on success, record action for future replays
    """
    logger.info(
        f"POST /claims/{claim_id}/{action} | actor={user.sub} role={user.role.value}"
    )

    # ── Step 1+2+3: C2.2 input guards ────────────────────────────
    check = validate_approval_input(
        actor=user.sub, claim_id=claim_id, action=action, reason=reason,
    )

    if check.cached_result is not None:
        # Idempotency hit — return same result a previous call produced.
        logger.info(
            f"POST /claims/{claim_id}/{action} idempotent replay | actor={user.sub}"
        )
        return ClaimStatusResponse(**check.cached_result)

    if check.violations:
        http_code = http_status_for(check.violations)
        detail = {
            "error": "Input validation failed",
            "violations": [v.model_dump() for v in check.violations],
        }
        headers = (
            {"Retry-After": str(check.retry_after_secs)}
            if check.retry_after_secs is not None else None
        )
        raise HTTPException(status_code=http_code, detail=detail, headers=headers)

    # ── Step 4: MCP write ────────────────────────────────────────
    try:
        row = await mcp_router.call(
            "claims", "update_claim_status",
            caller_role=user.role,
            claim_id=claim_id,
            new_status=new_status,
            actor=user.sub,
            note=reason,
        )
    except ValueError as e:
        logger.warning(f"POST /claims/{claim_id}/{action} not found: {e}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        if e.__class__.__name__ == "ClaimStateConflictError":
            logger.warning(f"POST /claims/{claim_id}/{action} conflict: {e}")
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
        logger.error(f"POST /claims/{claim_id}/{action} error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error updating claim",
        )

    # Record success for idempotency + rate counting
    record_approval_action(user.sub, claim_id, action, row)

    return ClaimStatusResponse(**row)
