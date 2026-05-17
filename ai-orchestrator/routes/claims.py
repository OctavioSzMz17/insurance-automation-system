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
import sys
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status

_ORCH_ROOT     = Path(__file__).resolve().parents[1]
_PROJECT_ROOT  = _ORCH_ROOT.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_ORCH_ROOT))

from packages.common.schemas.api import (  # noqa: E402
    ClaimSubmitRequest,
    ClaimSubmitResponse,
    ClaimStatusResponse,
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
