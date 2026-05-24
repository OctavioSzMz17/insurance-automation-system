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

import asyncio
import logging
import os
import sys
import time
import uuid
from collections import Counter
from pathlib import Path
from threading import Lock
from typing import Optional

_ORCH_ROOT_FOR_OBS    = Path(__file__).resolve().parents[1]
_PROJECT_ROOT_FOR_OBS = _ORCH_ROOT_FOR_OBS.parent
sys.path.insert(0, str(_PROJECT_ROOT_FOR_OBS))

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
from fastapi.responses import StreamingResponse

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
    ClaimConfirmRequest,
    ClaimConfirmResponse,
    ClaimTraceResponse,
    StreamTicketResponse,
    TraceObservationItem,
)

# Master Patch Part 3 — HMAC-signed trace markers (audit-trail integrity).
from packages.common.trace_signing import (  # noqa: E402
    extract_verified_trace_ids,
    sign_trace_marker,
)

from auth                  import (                                  # noqa: E402
    Role, UserPrincipal, require_role,
)
# Master Patch Part 4 — bounded stream-ticket store + quota exception.
from auth.stream_tickets    import (                                  # noqa: E402
    StreamTicket, StreamTicketStore, TicketQuotaExceeded,
)
from core.mcp_client        import MCPClientError                     # noqa: E402
from routes.mcp_router      import MCPRouter, get_mcp_router          # noqa: E402
# E1.3 — SSE streaming uses the same bus the agent_router publishes to.
from observability.event_bus import get_event_bus                     # noqa: E402
from validation             import (                                   # noqa: E402
    validate_approval_input,
    record_approval_action,
    http_status_for,
)
from config                 import get_settings                        # noqa: E402

logger = logging.getLogger(__name__)
router  = APIRouter(prefix="/claims", tags=["claims"])


# ── Fix B — pdf_path boundary guard ─────────────────────────────

def _assert_pdf_path_safe(pdf_path: str) -> None:
    """Raise HTTP 400 if pdf_path resolves outside _UPLOAD_DIR.

    Defence-in-depth layer that runs after the Pydantic schema validator
    (which already rejects '..', null bytes, and relative paths). This
    check catches OS-level symlink attacks and any platform-specific path
    representation that slips past the schema layer.

    Only applies to POST /claims/submit (JSON body path). The upload
    endpoints (/submit-upload, /reanalyze-upload) generate the path
    server-side and are not affected.

    Args:
        pdf_path: The validated string from ClaimSubmitRequest.

    Raises:
        HTTPException 400: Path resolves outside CLAIM_UPLOAD_DIR.
    """
    try:
        resolved = Path(pdf_path).resolve()
        allowed  = _UPLOAD_DIR.resolve()
        if not str(resolved).startswith(str(allowed)):
            logger.warning(
                "pdf_path boundary violation | path=%s resolved=%s allowed=%s",
                pdf_path, resolved, allowed,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="pdf_path must point to a file inside the upload directory",
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("pdf_path validation error | path=%s err=%r", pdf_path, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="pdf_path is invalid",
        )


# ── POST /claims/submit — per-user rate limit (Fix #79) ─────────────────────

_SUBMIT_RATE_LIMIT  = 10   # max submissions per window per user
_SUBMIT_RATE_WINDOW = 60   # sliding window (seconds)


async def _check_submit_rate_limit(request: Request, sub: str) -> None:
    """Enforce ≤ 10 claim submissions per 60 s per user (Redis sorted-set).

    Uses the same ZADD sliding-window pattern as approval_validators.py
    for consistency. Skips — with a WARNING — when Redis is not configured
    so that single-replica dev environments are never broken.

    Fix #79 — closes the unbounded-pipeline-load vector: without this
    guard a single ANALYST credential could spawn arbitrarily many
    expensive agent pipelines, exhausting CPU/DB connections.

    Args:
        request: FastAPI Request — used to reach app.state.redis.
        sub:     JWT sub (email) of the authenticated user.

    Raises:
        HTTPException 429: rate limit exceeded.
    """
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        logger.warning(
            "submit rate-limit skipped — no Redis configured | sub=%s", sub,
        )
        return

    key    = f"submit_rate:{sub}"
    now_ts = time.time()
    cutoff = now_ts - _SUBMIT_RATE_WINDOW

    pipe = redis.pipeline()
    pipe.zremrangebyscore(key, "-inf", cutoff)
    pipe.zcard(key)
    _, count = await pipe.execute()

    if count >= _SUBMIT_RATE_LIMIT:
        oldest = await redis.zrange(key, 0, 0, withscores=True)
        retry_after = _SUBMIT_RATE_WINDOW
        if oldest:
            oldest_ts = oldest[0][1]
            retry_after = max(1, int(oldest_ts + _SUBMIT_RATE_WINDOW - now_ts))
        logger.warning(
            "submit rate-limit BLOCKED | sub=%s count=%d limit=%d retry_after=%ds",
            sub, count, _SUBMIT_RATE_LIMIT, retry_after,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Submission rate limit exceeded "
                f"({_SUBMIT_RATE_LIMIT} per {_SUBMIT_RATE_WINDOW}s). "
                f"Retry after {retry_after}s."
            ),
            headers={"Retry-After": str(retry_after)},
        )

    pipe2 = redis.pipeline()
    pipe2.zadd(key, {str(now_ts): now_ts})
    pipe2.expire(key, _SUBMIT_RATE_WINDOW + 5)
    await pipe2.execute()


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
    # Fix A — always generate a server-side UUID4 as the pipeline id.
    # The X-Trace-Id header (observability) is intentionally NOT used here
    # — it is client-controllable and must never be a security principal key.
    request_id = str(uuid.uuid4())
    logger.info(
        f"POST /claims/submit | request_id={request_id} actor={user.sub} "
        f"role={user.role.value} pdf_path={body.pdf_path}"
    )

    # Fix #79 — per-user submission rate limit (10/min via Redis ZADD).
    # Placed BEFORE the pipeline starts so rejected requests never allocate
    # agent resources.
    await _check_submit_rate_limit(request, user.sub)

    # Fix B — defence-in-depth: even after the Pydantic validator rejects
    # '..' and relative paths, confirm the resolved path is inside the
    # allowed upload directory. This catches symlink attacks and any edge
    # case the schema validator might miss on different OS path flavours.
    _assert_pdf_path_safe(body.pdf_path)

    # Master Patch Part 2 — record ownership BEFORE the pipeline starts.
    # The SSE stream-ticket mint endpoint will consult this registry to
    # reject cross-user stream hijack attempts. Done first so a fast
    # subscriber can connect mid-pipeline without a TOCTOU window.
    await _bind_request_owner(request, request_id, user)

    agent_router = getattr(request.app.state, "agent_router", None)
    if agent_router is None:
        logger.error(f"AgentRouter not initialised | request_id={request_id}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent router not ready — try again shortly.",
        )

    result = await agent_router.process_claim(
        body.pdf_path,
        trace_name="claim-submit",
        session_id=request_id,
        actor=user.sub,
        trace_metadata={"endpoint": "/claims/submit", "request_id": request_id},
    )

    recommendation: str | None = None
    persisted_id: int | None = None
    claim_status_persisted: str | None = None
    summary: str | None = None
    if result.claim_decision:
        recommendation = result.claim_decision.get("decision")
    if result.triage_report:
        summary = result.triage_report.get("summary")

    # Rule 6 — HITL gate: orchestrator (deterministic code) inserts the claim
    # row with AWAITING_INPUT. The agent never calls create_claim directly.
    if result.pipeline_status == "SUCCESS" and result.claim_decision:
        dec = result.claim_decision

        # Master Patch Part 2 — block insider fraud where an employee
        # with low role privilege submits a PDF whose `Asegurado: ID:`
        # line names another customer. If the JWT actor's identity
        # does not match the email on the target user_id, refuse to
        # persist. ADMINs are exempt for ops / backfill scenarios.
        target_uid = dec.get("user_id")
        if isinstance(target_uid, int):
            await _assert_actor_may_submit_for(
                user=user, target_user_id=target_uid,
                mcp_router=mcp_router, request_id=request_id,
            )

        if dec.get("policy_id") is not None:
            # Master Patch Part 3 — sign the trace marker on the
            # orchestrator side (where PROPOSAL_SIGNING_KEY lives) and
            # prepend it to ai_reasoning BEFORE it crosses the MCP
            # boundary. The claims MCP can no longer mint markers itself.
            signed_reasoning = (
                f"{sign_trace_marker(request_id)}\n\n{dec['ai_reasoning']}"
                if request_id else dec["ai_reasoning"]
            )
            try:
                created = await mcp_router.call(
                    "claims", "create_claim",
                    internal=True,  # orchestrator-initiated, not user-driven
                    user_id=dec["user_id"],
                    policy_id=dec["policy_id"],
                    incident_date=dec["incident_date"],
                    incident_description=dec["incident_description"],
                    incident_location=dec.get("incident_location"),
                    severity_label=dec["severity_label"],
                    reported_damage_amount=(
                        str(dec["reported_damage_amount"])
                        if dec.get("reported_damage_amount") else None
                    ),
                    claim_status="AWAITING_INPUT",
                    ai_reasoning=signed_reasoning,
                )
                persisted_id = created.get("claim_id")
                claim_status_persisted = "AWAITING_INPUT"
            except Exception as e:
                logger.error(
                    f"POST /claims/submit | create_claim failed | "
                    f"request_id={request_id} err={e}"
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
# Fix #73 — reads from Settings (CLAIM_UPLOAD_DIR in .env) so the path
# is validated at startup via the lifespan guard in app.py.
# In production this must be a PVC mounted by both orchestrator + docs MCP.
_UPLOAD_DIR = Path(get_settings().claim_upload_dir or "/tmp/claim-uploads")
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
    # Fix A — server-side UUID4; never derived from X-Trace-Id.
    request_id = str(uuid.uuid4())
    logger.info(
        f"POST /claims/submit-upload | request_id={request_id} actor={user.sub} "
        f"role={user.role.value} filename={pdf.filename}"
    )

    # Fix #79 — same per-user submission rate limit as /submit (10/min).
    await _check_submit_rate_limit(request, user.sub)

    # Master Patch Part 2 — bind ownership immediately so the SSE stream
    # subscriber path can enforce tenant isolation, even before we've
    # validated the upload payload.
    await _bind_request_owner(request, request_id, user)

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
        result = await agent_router.process_claim(
            str(target),
            trace_name="claim-submit-upload",
            session_id=request_id,
            actor=user.sub,
            trace_metadata={
                "endpoint":   "/claims/submit-upload",
                "request_id": request_id,
                "filename":   pdf.filename,
                "bytes":      bytes_written,
            },
        )
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
    if result.triage_report:
        summary = result.triage_report.get("summary")

    # Rule 6 — HITL gate: orchestrator inserts AWAITING_INPUT, not the agent.
    if result.pipeline_status == "SUCCESS" and result.claim_decision:
        dec = result.claim_decision

        # Master Patch Part 2 — same insider-fraud guard as /submit.
        # Refuse to persist a claim whose target user_id does not belong
        # to the JWT actor's identity (admins exempt).
        target_uid = dec.get("user_id")
        if isinstance(target_uid, int):
            await _assert_actor_may_submit_for(
                user=user, target_user_id=target_uid,
                mcp_router=mcp_router, request_id=request_id,
            )

        if dec.get("policy_id") is not None:
            # Master Patch Part 3 — same signed-marker stamping as /submit.
            signed_reasoning = (
                f"{sign_trace_marker(request_id)}\n\n{dec['ai_reasoning']}"
                if request_id else dec["ai_reasoning"]
            )
            try:
                created = await mcp_router.call(
                    "claims", "create_claim",
                    internal=True,
                    user_id=dec["user_id"],
                    policy_id=dec["policy_id"],
                    incident_date=dec["incident_date"],
                    incident_description=dec["incident_description"],
                    incident_location=dec.get("incident_location"),
                    severity_label=dec["severity_label"],
                    reported_damage_amount=(
                        str(dec["reported_damage_amount"])
                        if dec.get("reported_damage_amount") else None
                    ),
                    claim_status="AWAITING_INPUT",
                    ai_reasoning=signed_reasoning,
                )
                persisted_id = created.get("claim_id")
                claim_status_persisted = "AWAITING_INPUT"
            except Exception as e:
                logger.error(
                    f"POST /claims/submit-upload | create_claim failed | "
                    f"request_id={request_id} err={e}"
                )

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

    # Fix #70 — ANALYST role is scoped to their own claims (email = JWT sub).
    # AUDITOR and ADMIN receive all claims unfiltered.
    analyst_email = user.sub if user.role == Role.ANALYST else None

    try:
        rows = await mcp_router.call(
            "claims", "list_claims",
            caller_role=user.role,
            status=status_filter,
            limit=limit,
            offset=offset,
            **({"user_email": analyst_email} if analyst_email else {}),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except MCPClientError as e:
        # Master Patch Part 4 — internal detail to logs only.
        logger.error(
            "GET /claims upstream failure | internal=%s", e.internal,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=e.public,
        )
    except RuntimeError as e:
        logger.error(f"GET /claims DB error: {e!r}")
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
    except MCPClientError as e:
        logger.error(
            "GET /claims/%s upstream failure | internal=%s",
            claim_id, e.internal, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=e.public,
        )
    except RuntimeError as e:
        logger.error(f"GET /claims/{claim_id} DB error: {e!r}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error retrieving claim",
        )

    # Fix #71 — ANALYST can only read claims belonging to their own account.
    # Resolve the claim's user_id to an email via legacy-provider and compare
    # against the JWT sub (which equals the authenticated user's email).
    # AUDITOR and ADMIN bypass this check.
    if user.role == Role.ANALYST:
        try:
            claim_user = await mcp_router.call(
                "legacy-provider", "get_user_by_id",
                caller_role=user.role,
                user_id=row["user_id"],
            )
            target_email = (claim_user.get("email") or "").strip().lower()
            actor_sub    = user.sub.strip().lower()
            if not target_email or target_email != actor_sub:
                logger.warning(
                    f"GET /claims/{claim_id} DENIED cross-user | "
                    f"actor={user.sub} owner_email={target_email!r}"
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You are not authorised to view this claim",
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(
                "GET /claims/%s ownership check failed | actor=%s err=%s",
                claim_id, user.sub, exc,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not verify claim ownership",
            )

    return ClaimStatusResponse(**row)


# ── GET /claims/{claim_id}/trace (D5.1) ──────────────────────────

# Master Patch Part 3 — _extract_trace_ids now returns ONLY verified
# (HMAC-SHA256-signed) markers. Unsigned legacy markers are silently
# dropped — fail-safe: the UI sees "no trace" rather than a spoofable
# pointer to an attacker-chosen Langfuse session.
def _extract_trace_ids(ai_reasoning: Optional[str]) -> list[str]:
    """Return every verified [trace_id: TID.SIG] marker, oldest first.

    Order in the string IS the chronological order, so the last element
    is the most recent (re-)analysis. Markers that do not verify under
    the current PROPOSAL_SIGNING_KEY are dropped, defeating audit-trail
    spoofing via append_reasoning.
    """
    return extract_verified_trace_ids(ai_reasoning)


def _shape_observation(o: dict) -> TraceObservationItem:
    """Map a raw Langfuse observation dict → our flatter schema."""
    usage = o.get("usage") or {}
    latency = o.get("latency")  # seconds (float) in Langfuse
    return TraceObservationItem(
        id=str(o.get("id") or ""),
        parent_id=o.get("parentObservationId"),
        name=o.get("name"),
        type=o.get("type"),
        started_at=o.get("startTime"),
        ended_at=o.get("endTime"),
        latency_ms=int(latency * 1000) if isinstance(latency, (int, float)) else None,
        cost_usd=str(o["totalCost"]) if o.get("totalCost") is not None else None,
        input_tokens=usage.get("input") if isinstance(usage, dict) else None,
        output_tokens=usage.get("output") if isinstance(usage, dict) else None,
        level=o.get("level"),
        status_message=o.get("statusMessage"),
    )


@router.get(
    "/{claim_id}/trace",
    response_model=ClaimTraceResponse,
    status_code=status.HTTP_200_OK,
    summary="Fetch Lía's trace for a claim (proxy to Langfuse)",
    description=(
        "Returns the most recent Langfuse trace associated with the "
        "claim — LLM calls, tool calls, latencies, costs, tokens. "
        "Used by the UI to render the reasoning tree inline so "
        "adjusters never need to open the Langfuse dashboard. "
        "Requires `analyst` role+."
    ),
    responses={
        401: {"description": "Missing or invalid token"},
        403: {"description": "Insufficient role"},
        404: {"description": "Claim not found OR no trace stored for this claim"},
        502: {"description": "Langfuse is unreachable or returned an error"},
        503: {"description": "Observability not configured"},
    },
)
async def get_claim_trace(
    claim_id:   int,
    user:       UserPrincipal = Depends(require_role(Role.ANALYST)),
    mcp_router: MCPRouter     = Depends(get_mcp_router),
) -> ClaimTraceResponse:
    logger.info(
        f"GET /claims/{claim_id}/trace | actor={user.sub} role={user.role.value}"
    )

    # 1) Load the claim — gives us ai_reasoning + a 404 if it doesn't exist.
    try:
        row = await mcp_router.call(
            "claims", "get_claim_by_id",
            caller_role=user.role, claim_id=claim_id,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Claim {claim_id} not found",
        )
    except MCPClientError as e:
        logger.error(
            "GET /claims/%s/trace upstream failure | internal=%s",
            claim_id, e.internal, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=e.public,
        )

    # 2) Pick the most recent trace_id marker.
    markers = _extract_trace_ids(row.get("ai_reasoning"))
    if not markers:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No trace recorded for claim {claim_id}. "
                f"Likely created before observability was wired."
            ),
        )
    trace_session_id = markers[-1]  # last marker = most recent run

    # 3) Lazy-build the Langfuse client. Returns None if disabled.
    from observability.langfuse_client import (
        get_langfuse_client_from_settings, LangfuseUnavailable,
    )
    lf = get_langfuse_client_from_settings()
    if lf is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Langfuse is not configured on this orchestrator.",
        )

    # 4) Find the trace by session id. We stored session_id == trace_id
    #    when we wired the callback in agent_router, but Langfuse's own
    #    trace ids are different opaque strings — we have to look them
    #    up via the session filter.
    try:
        traces = await lf.list_traces_by_session(trace_session_id, limit=1)
    except LangfuseUnavailable as e:
        logger.warning(f"GET /claims/{claim_id}/trace | Langfuse down: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Langfuse is currently unreachable.",
        )
    if not traces:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Trace session {trace_session_id} not found in Langfuse "
                f"(retention may have expired)."
            ),
        )

    head = traces[0]
    langfuse_trace_id = head.get("id") or ""

    # 5) Fetch the full trace (with observations) by its actual id.
    try:
        detail = await lf.get_trace(langfuse_trace_id)
    except LangfuseUnavailable as e:
        logger.warning(
            f"GET /claims/{claim_id}/trace | get_trace failed: {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Langfuse returned an error fetching the trace.",
        )

    # 6) Build the browser deep-link (LANGFUSE_PUBLIC_URL because the
    #    internal docker hostname is unreachable from the user's browser).
    #
    # D5.3 — gate the URL by role. The Langfuse dashboard exposes raw
    # prompts, tool inputs/outputs, retry trees — engineer-level detail
    # the adjuster shouldn't see (it's confusing and could leak prompts
    # that contain PII). Only `admin` callers get the link; for everyone
    # else we send `null` and the UI's existing null-check hides the
    # button. Defense-in-depth: response itself doesn't contain the URL.
    from config import get_settings
    settings = get_settings()
    langfuse_url: Optional[str] = None
    if user.role == Role.ADMIN:
        public_base = (
            settings.langfuse_public_url
            or settings.langfuse_host  # last-resort fallback
        ).rstrip("/")
        project_id = "orchestrator-dev"  # matches LANGFUSE_INIT_PROJECT_ID
        langfuse_url = (
            f"{public_base}/project/{project_id}/traces/{langfuse_trace_id}"
        )

    observations = [_shape_observation(o) for o in (detail.get("observations") or [])]

    latency_s = detail.get("latency")
    total_cost = detail.get("totalCost")
    return ClaimTraceResponse(
        claim_id=claim_id,
        trace_id=trace_session_id,
        name=detail.get("name"),
        total_latency_ms=int(latency_s * 1000) if isinstance(latency_s, (int, float)) else None,
        total_cost_usd=str(total_cost) if total_cost is not None else None,
        started_at=detail.get("timestamp"),
        ended_at=None,  # Langfuse trace-level doesn't expose ended_at directly
        langfuse_url=langfuse_url,
        observations=observations,
    )


# ── SSE stream-ticket plumbing (Fix 3 + Master Patch Part 4) ────

import json as _json_stream  # local alias — module already imports json elsewhere

# Authoritative TTL — must match StreamTicketStore configured in app.py.
# Kept here for log messages / Retry-After headers only; the store owns
# the actual TTL enforcement.
_TICKET_TTL_SECONDS: int = 30

# ── Master Patch Part 4 — concurrent SSE stream caps ────────────────
# Per-sub limit on simultaneously-open SSE connections. Combined with
# the per-stream lifetime cap below this bounds total fd / coroutine
# pressure to (max_subs × MAX_STREAMS_PER_SUB) at any instant.
_MAX_STREAMS_PER_SUB:   int   = 3
# Hard upper bound on a single SSE generator's wall-clock lifetime.
# Pipelines that legitimately exceed this should re-subscribe with a
# fresh ticket; the client UI auto-handles that.
_MAX_STREAM_LIFETIME_S: float = 120.0
# Fix #77 — Redis key TTL for the stream counter. Slightly longer than the
# max stream lifetime so pod crashes don't leave stale counts forever.
_STREAM_COUNT_TTL: int = int(_MAX_STREAM_LIFETIME_S) + 30

_active_streams_lock: Lock = Lock()
_active_streams_by_sub: Counter[str] = Counter()


async def _stream_admit(sub: str, redis=None) -> None:
    """Reserve one concurrent-stream slot for `sub`. 429 on overflow.

    Fix #77 — uses Redis INCR when available so the cap is enforced
    consistently across all orchestrator replicas. Falls back to the
    in-memory Counter when Redis is not configured (dev/test).

    Args:
        sub:   JWT subject of the connecting user.
        redis: redis.asyncio.Redis client from app.state, or None.
    """
    if redis is not None:
        key   = f"stream_count:{sub}"
        count = await redis.incr(key)
        if count > _MAX_STREAMS_PER_SUB:
            await redis.decr(key)   # rollback — INCR is not a reservation
            logger.warning(
                "SSE stream DENIED | sub=%s active=%d cap=%d backend=redis",
                sub, count - 1, _MAX_STREAMS_PER_SUB,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Too many concurrent SSE streams "
                    f"(cap={_MAX_STREAMS_PER_SUB} per user)"
                ),
                headers={"Retry-After": "5"},
            )
        # Refresh the pod-crash-safety TTL on every successful admit.
        await redis.expire(key, _STREAM_COUNT_TTL)
        return

    # In-memory fallback (single-replica dev/test).
    with _active_streams_lock:
        current = _active_streams_by_sub[sub]
        if current >= _MAX_STREAMS_PER_SUB:
            logger.warning(
                "SSE stream DENIED | sub=%s active=%d cap=%d backend=in-memory",
                sub, current, _MAX_STREAMS_PER_SUB,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Too many concurrent SSE streams "
                    f"(cap={_MAX_STREAMS_PER_SUB} per user)"
                ),
                headers={"Retry-After": "5"},
            )
        _active_streams_by_sub[sub] = current + 1


async def _stream_release(sub: str, redis=None) -> None:
    """Release one concurrent-stream slot. Safe to call multiple times.

    Fix #77 — mirrors _stream_admit; decrements the Redis INCR counter
    and deletes the key when it reaches zero to avoid stale single-digit
    counts persisting past the TTL.

    Args:
        sub:   JWT subject whose slot is being freed.
        redis: redis.asyncio.Redis client from app.state, or None.
    """
    if redis is not None:
        key       = f"stream_count:{sub}"
        new_count = await redis.decr(key)
        if new_count <= 0:
            await redis.delete(key)
        return

    # In-memory fallback.
    with _active_streams_lock:
        new_value = _active_streams_by_sub[sub] - 1
        if new_value <= 0:
            _active_streams_by_sub.pop(sub, None)
        else:
            _active_streams_by_sub[sub] = new_value


def _get_ticket_store(request: Request) -> StreamTicketStore:
    """Resolve the bounded ticket store from app.state. 503 if missing."""
    store = getattr(request.app.state, "stream_tickets", None)
    if not isinstance(store, StreamTicketStore):
        logger.error("StreamTicketStore missing on app.state")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server not fully initialised (ticket store missing).",
        )
    return store


async def _mint_ticket(request: Request, request_id: str, user: UserPrincipal) -> str:
    """Mint a ticket via the bounded store. Maps quota errors to 429.

    Master Patch Part 4 — store enforces global maxsize=10k, TTL=30s,
    and per-sub cap of 20 concurrent unconsumed tickets.
    Fix #64 — declared async so `await store.mint(...)` inside is legal.
    """
    store = _get_ticket_store(request)
    try:
        ticket_id = await store.mint(sub=user.sub, role=user.role, request_id=request_id)
    except TicketQuotaExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Stream-ticket quota exceeded for this user "
                f"({exc.current_count}/{exc.cap}). Wait for outstanding "
                f"tickets to expire."
            ),
            headers={"Retry-After": str(exc.retry_after_s)},
        )

    logger.info(
        f"stream-ticket minted | ticket={ticket_id[:8]}… "
        f"request_id={request_id} actor={user.sub} ttl={_TICKET_TTL_SECONDS}s"
    )
    return ticket_id


async def _consume_ticket(
    request: Request, ticket_id: str, request_id: str
) -> UserPrincipal:
    """Validate and atomically burn a stream ticket.

    Raises HTTPException on any failure — invalid UUID, expired,
    already consumed, scope mismatch, or owner-registry mismatch.
    On success returns the UserPrincipal encoded at mint time.
    """
    store = _get_ticket_store(request)
    ticket: Optional[StreamTicket] = await store.pop(ticket_id)  # atomic single-use burn

    if ticket is None:
        logger.warning(
            f"stream-ticket rejected | ticket={ticket_id[:8]}… "
            f"reason=not_found_expired_or_already_used"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, expired, or already-used stream ticket",
        )
    # Defence-in-depth — the store also TTL-evicts, but a ticket that
    # just slipped through the eviction window is still rejectable here.
    # Fix #80 — compare wall-clock (time.time()) to match the wall-clock
    # expires_at stored by both local and Redis backends.
    if time.time() > ticket.expires_at:
        logger.warning(
            f"stream-ticket rejected | ticket={ticket_id[:8]}… reason=expired"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Stream ticket expired (TTL={_TICKET_TTL_SECONDS}s)",
        )
    if ticket.request_id != request_id:
        logger.warning(
            f"stream-ticket rejected | ticket={ticket_id[:8]}… "
            f"reason=scope_mismatch expected={ticket.request_id} got={request_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ticket is not scoped to this stream",
        )

    # Master Patch Part 2 — defense-in-depth: even if the mint endpoint
    # ever regresses, the consume path independently cross-checks the
    # ticket's sub against the canonical owner registry. This guarantees
    # `user.sub == ticket.sub == recorded_owner_of(request_id)` before
    # the SSE upgrade exposes any pipeline events.
    registry = getattr(request.app.state, "request_owners", None)
    if registry is not None:
        recorded_owner = await registry.get(request_id)
        if recorded_owner is None:
            logger.warning(
                f"stream-ticket rejected | ticket={ticket_id[:8]}… "
                f"reason=request_id_unknown_in_owner_registry"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Unknown request_id (no pipeline recorded)",
            )
        if recorded_owner != ticket.sub and ticket.role != Role.ADMIN:
            logger.warning(
                f"stream-ticket rejected | ticket={ticket_id[:8]}… "
                f"reason=owner_mismatch ticket_sub={ticket.sub} "
                f"recorded_owner={recorded_owner}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Ticket subject does not match the pipeline owner",
            )

    user = UserPrincipal(sub=ticket.sub, role=ticket.role)
    # Invariant: the reconstituted UserPrincipal must equal the ticket's
    # encoded identity. Future refactors that change the reconstruction
    # path will trip this assertion in tests.
    assert user.sub == ticket.sub, "ticket sub reconstruction mismatch"

    logger.info(
        f"stream-ticket consumed | ticket={ticket_id[:8]}… "
        f"request_id={request_id} actor={ticket.sub}"
    )
    return user


# ── Master Patch Part 2 — ownership + identity helpers ───────────

async def _bind_request_owner(
    request: Request, request_id: str, user: UserPrincipal,
) -> None:
    """Record `user.sub` as the owner of `request_id` in the registry.

    Called by submit_claim / submit_claim_upload BEFORE the agent
    pipeline starts so any concurrent stream-ticket mint by another
    user is immediately rejectable.

    Fix A — now async (registry.set is async for Redis backend) and
    raises 409 on OwnerConflictError instead of silently overwriting.
    """
    from auth.request_owners import OwnerConflictError  # local import avoids circular

    registry = getattr(request.app.state, "request_owners", None)
    if registry is None:
        logger.error(
            f"request_owners registry missing on app.state | request_id={request_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server not fully initialised (owner registry missing).",
        )
    try:
        await registry.set(request_id, user.sub)
    except OwnerConflictError:
        # UUID4 collision is cryptographically negligible.
        # A conflict here means an attacker supplied a crafted request_id.
        logger.warning(
            f"request_id ownership conflict | request_id={request_id} sub={user.sub}"
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="request_id already in use by another pipeline.",
        )
    logger.debug(
        f"request owner bound | request_id={request_id} sub={user.sub}"
    )


async def _assert_actor_may_submit_for(
    *,
    user:        UserPrincipal,
    target_user_id: int,
    mcp_router:  MCPRouter,
    request_id:  str,
) -> None:
    """Refuse the request unless `user.sub` matches the email on
    `target_user_id` (or `user` is ADMIN).

    Closes the Insider User Fraud vulnerability: without this gate, an
    employee with only ANALYST privilege could submit a PDF whose
    `Asegurado: ID:` line names ANY customer and persist a claim
    against that customer. With this gate, the JWT actor must be the
    customer themself (self-service) OR an admin (ops / backfill).

    Args:
        user:           Authenticated principal from JWT.
        target_user_id: user_id parsed from the PDF / decided by agent.
        mcp_router:     For the read-only users.get_user_by_id call.
        request_id:     For correlated logging.

    Raises:
        HTTPException 403: actor identity does not match target customer.
        HTTPException 502: users MCP unreachable; fail closed (do not
                           persist a claim whose ownership we can't verify).
    """
    if user.role == Role.ADMIN:
        return  # ops / backfill bypass — explicitly authorised role

    try:
        u = await mcp_router.call(
            "legacy-provider", "get_user_by_id",
            caller_role=user.role, user_id=target_user_id,
        )
    except ValueError:
        # Unknown user — the agent hallucinated or the PDF lied.
        logger.warning(
            f"submit DENIED | request_id={request_id} actor={user.sub} "
            f"reason=target_user_not_found target_uid={target_user_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorised to file claims for this user_id",
        )
    except Exception as e:
        logger.error(
            f"submit DENIED | request_id={request_id} actor={user.sub} "
            f"reason=users_mcp_error err={e!s}"
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not verify claim ownership upstream.",
        )

    target_email = (u.get("email") or "").strip().lower()
    actor_sub = user.sub.strip().lower()
    if not target_email or target_email != actor_sub:
        logger.warning(
            f"submit DENIED cross-user | request_id={request_id} "
            f"actor={user.sub} target_uid={target_user_id} "
            f"target_email={target_email!r}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorised to file claims for this user_id",
        )

    logger.info(
        f"submit ownership OK | request_id={request_id} actor={user.sub} "
        f"target_uid={target_user_id}"
    )


# ── POST /claims/{request_id}/stream-ticket ──────────────────────

@router.post(
    "/{request_id}/stream-ticket",
    response_model=StreamTicketResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Mint a short-lived single-use stream ticket for SSE auth",
    description=(
        "Client sends its JWT in the standard `Authorization: Bearer` header "
        "and receives a narrow-scoped, single-use ticket valid for "
        f"{_TICKET_TTL_SECONDS} seconds. The ticket is then passed as "
        "`?ticket=UUID` when opening the EventSource SSE connection, "
        "preventing JWT leakage into logs, proxies, and browser history. "
        "Requires `analyst` role+."
    ),
    responses={
        401: {"description": "Missing or invalid JWT"},
        403: {"description": "Insufficient role"},
    },
)
async def create_stream_ticket(
    request_id: str,
    request:    Request,
    user:       UserPrincipal = Depends(require_role(Role.ANALYST)),
) -> StreamTicketResponse:
    """Mint a single-use SSE ticket scoped to `request_id`.

    Master Patch Part 2 — cross-checks the JWT actor against the canonical
    pipeline-owner registry. Without this, any valid ANALYST could mint a
    ticket for another user's in-flight pipeline (request_ids leak via
    logs, X-Trace-Id, Langfuse) and exfiltrate the entire SSE feed.
    """
    registry = getattr(request.app.state, "request_owners", None)
    if registry is None:
        logger.error(
            f"stream-ticket DENIED | reason=registry_missing request_id={request_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server not fully initialised (owner registry missing).",
        )

    owner = await registry.get(request_id)
    if owner is None:
        logger.warning(
            f"stream-ticket DENIED | reason=unknown_request_id "
            f"request_id={request_id} actor={user.sub}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown request_id (no pipeline recorded or expired)",
        )

    if owner != user.sub and user.role != Role.ADMIN:
        logger.warning(
            f"stream-ticket DENIED cross-user | request_id={request_id} "
            f"actor={user.sub} owner={owner}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this pipeline",
        )

    ticket_id = await _mint_ticket(request, request_id, user)
    return StreamTicketResponse(ticket=ticket_id, scoped_to=request_id)


# ── GET /claims/stream/{request_id} (SSE, Phase E1.3) ────────────

@router.get(
    "/stream/{request_id}",
    summary="Subscribe to Lía's live event stream for a request (SSE)",
    description=(
        "Server-Sent Events feed of pipeline events keyed by `request_id`. "
        "Authentication uses a short-lived single-use ticket obtained from "
        "`POST /claims/{request_id}/stream-ticket` — pass it as `?ticket=UUID`. "
        "The ticket is burned on first connection; JWT is never in the URL. "
        "Each event line is `data: {json}`; stream ends on "
        "`pipeline.done` or `pipeline.error`. Requires `analyst` role+."
    ),
    responses={
        401: {"description": "Missing, invalid, or already-used ticket"},
        403: {"description": "Ticket scope mismatch or insufficient role"},
    },
)
async def stream_claim_events(
    request_id: str,
    request:    Request,
    ticket:     str = Query(
        ...,
        description=(
            "Single-use stream ticket from POST /claims/{request_id}/stream-ticket. "
            "Valid for 30 seconds, scoped to this request_id, burned on use."
        ),
    ),
) -> StreamingResponse:
    user = await _consume_ticket(request, ticket, request_id)

    # Fix #77 — capture the Redis client once so both _stream_admit and
    # the generator's _stream_release closure share the same reference.
    _redis = getattr(request.app.state, "redis", None)

    # Master Patch Part 4 — reserve a concurrent-stream slot for this
    # subject BEFORE constructing the generator. If admission fails
    # (429) we return without ever materialising a bus entry.
    await _stream_admit(user.sub, redis=_redis)

    logger.info(
        f"GET /claims/stream/{request_id} | actor={user.sub} role={user.role.value} "
        f"max_lifetime_s={_MAX_STREAM_LIFETIME_S}"
    )

    bus = get_event_bus()

    async def event_generator():
        # First chunk: an immediate keepalive so the browser knows the
        # connection is established (some proxies buffer the first event).
        yield ": connected\n\n"
        deadline = time.monotonic() + _MAX_STREAM_LIFETIME_S
        yielded_anything = False
        timed_out = False
        try:
            # Master Patch Part 4 — require_existing=True prevents the
            # bus from materialising an empty queue for an unknown
            # request_id (closes the connection-hoarding DoS).
            async for ev in bus.subscribe(request_id, require_existing=True):
                # Master Patch Part 4 — hard lifetime cap. Checked at
                # every loop iteration (which happens at least every
                # keepalive_s seconds), so the actual close lag is
                # bounded by ~15s past the deadline.
                if time.monotonic() > deadline:
                    timed_out = True
                    logger.info(
                        f"SSE generator | request_id={request_id} "
                        f"actor={user.sub} closing reason=max_lifetime "
                        f"limit_s={_MAX_STREAM_LIFETIME_S}"
                    )
                    err = {
                        "type": "pipeline.timeout",
                        "request_id": request_id,
                        "data": {
                            "reason": "max_stream_lifetime",
                            "limit_s": _MAX_STREAM_LIFETIME_S,
                        },
                    }
                    yield (
                        "event: pipeline.timeout\n"
                        f"data: {_json_stream.dumps(err)}\n\n"
                    )
                    return

                if ev is None:
                    # Keepalive tick — SSE comments are ignored by the client.
                    yield ": keepalive\n\n"
                    continue
                yielded_anything = True
                # SSE format: optional `event:` name, then `data:` payload.
                ev_type = ev.get("type") or "message"
                yield (
                    f"event: {ev_type}\n"
                    f"data: {_json_stream.dumps(ev, ensure_ascii=False)}\n\n"
                )
        except asyncio.CancelledError:
            # Client disconnected — graceful close path. Re-raise so
            # asyncio's cancellation contract is honoured.
            logger.info(
                f"SSE generator | request_id={request_id} actor={user.sub} "
                f"closing reason=client_disconnect"
            )
            raise
        except Exception as e:
            # Master Patch Part 4 — log the verbose error internally but
            # send only a sanitised payload to the client. No raw
            # exception strings ever cross the wire.
            logger.warning(
                f"SSE generator | request_id={request_id} actor={user.sub} "
                f"unexpected error: {e!r}",
                exc_info=True,
            )
            err = {
                "type": "pipeline.error",
                "request_id": request_id,
                "data": {"error": "stream_terminated_unexpectedly"},
            }
            yield (
                "event: pipeline.error\n"
                f"data: {_json_stream.dumps(err)}\n\n"
            )
        finally:
            # Always release the concurrent-stream slot, even on timeout
            # or client disconnect. This is the only place the counter
            # is decremented. Fix #77 — async release (Redis DECR or in-memory).
            await _stream_release(user.sub, redis=_redis)
            if not yielded_anything and not timed_out:
                # Master Patch Part 4 — subscriber connected against an
                # unknown / dead request_id. Emit one diagnostic event
                # so the UI can tell the user the stream is gone, then
                # close. No queue was ever materialised.
                logger.info(
                    f"SSE generator | request_id={request_id} actor={user.sub} "
                    f"closing reason=unknown_or_expired_request_id"
                )

    headers = {
        "Cache-Control":     "no-cache",
        "Connection":        "keep-alive",
        # Disable proxy buffering so events flush to the client immediately.
        # Honoured by nginx ingress; ignored elsewhere.
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=headers,
    )


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
    except MCPClientError as e:
        logger.error(
            "_verify_claim_exists upstream failure | claim_id=%s internal=%s",
            claim_id, e.internal, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=e.public,
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

    # Fix A — always a fresh server-side UUID4; never from X-Trace-Id.
    reanalysis_request_id = str(uuid.uuid4())
    result = await agent_router.process_claim(
        str(pdf_path),
        persist=False,
        trace_name="claim-reanalyze",
        session_id=reanalysis_request_id,
        actor=actor,
        trace_metadata={
            "endpoint":   "/claims/{id}/reanalyze",
            "claim_id":   claim_id,
            "request_id": reanalysis_request_id,
        },
    )

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
    # D5.1 — stamp this re-analysis's trace_id so the UI can show the
    # MOST RECENT Lía run (not just the original submit). The marker
    # sits inside the re-analysis block so extractor knows which trace
    # belongs to which event.
    # Master Patch Part 3 — the marker is HMAC-SHA256 signed. The
    # claims MCP's append_reasoning tool rejects writes whose embedded
    # markers don't verify, so spoofing the trace pointer is no longer
    # possible from any MCP caller.
    trace_marker = (
        f"\n{sign_trace_marker(reanalysis_request_id)}"
        if reanalysis_request_id else ""
    )
    block = (
        f"[RE-ANALYSIS — {ts} by {actor}]\n"
        f"Recommendation: {decision}. "
        f"{notes_payload['auditor_notes'] or summary or 'No additional notes.'}\n"
        f"Raw: {_json.dumps(notes_payload, ensure_ascii=False)}"
        f"{trace_marker}"
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
    except MCPClientError as e:
        logger.error(
            "reanalyze append_reasoning upstream failure | claim=%s internal=%s",
            claim_id, e.internal, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Re-analysis succeeded but could not be persisted.",
        )
    except Exception as e:
        logger.error(
            f"reanalyze append_reasoning failed | claim={claim_id} err={e!r}",
            exc_info=True,
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


# ── POST /claims/{claim_id}/confirm (Rule 6 HITL gate) ───────────

@router.post(
    "/{claim_id}/confirm",
    response_model=ClaimConfirmResponse,
    status_code=status.HTTP_200_OK,
    summary="Human auditor confirms a claim decision (Rule 6 HITL gate)",
    description=(
        "Unified HITL endpoint. The human auditor reviews the AI proposal "
        "and commits the final decision: APPROVED, REJECTED, or MANUAL_REVIEW. "
        "The claim must be in AWAITING_INPUT status. Requires `auditor` role+. "
        "Actor identity is taken from the JWT `sub` — never from the body."
    ),
    responses={
        401: {"description": "Missing or invalid token"},
        403: {"description": "Insufficient role"},
        404: {"description": "No claim exists with the given claim_id"},
        409: {"description": "Claim is not in AWAITING_INPUT state"},
        500: {"description": "Database error"},
    },
)
async def confirm_claim(
    claim_id:   int,
    body:       ClaimConfirmRequest,
    user:       UserPrincipal = Depends(require_role(Role.AUDITOR)),
    mcp_router: MCPRouter     = Depends(get_mcp_router),
) -> ClaimConfirmResponse:
    """Human auditor commits the final decision for an AWAITING_INPUT claim."""
    logger.info(
        f"POST /claims/{claim_id}/confirm | actor={user.sub} "
        f"role={user.role.value} decision={body.decision}"
    )
    row = await _decide_claim(
        claim_id=claim_id,
        action="confirm",
        new_status=body.decision,
        reason=body.reason,
        user=user,
        mcp_router=mcp_router,
    )
    return ClaimConfirmResponse(
        claim_id=claim_id,
        new_status=row.claim_status,
        actor=user.sub,
    )


# ── Shared helper for /approve, /reject and /confirm (Rule 6 + C2.2 guards) ─

async def _decide_claim(
    *,
    claim_id:   int,
    action:     str,                  # "approve" | "reject" | "confirm"
    new_status: str,                  # "APPROVED" | "REJECTED" | "MANUAL_REVIEW"
    reason:     str,
    user:       UserPrincipal,
    mcp_router: MCPRouter,
) -> ClaimStatusResponse:
    """Run C2.2 input guards, then route to MCP. Used by approve + reject + confirm.

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
    # Fix D: validate_approval_input is now async (Redis rate/idempotency).
    check = await validate_approval_input(
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
    except MCPClientError as e:
        logger.error(
            "POST /claims/%s/%s upstream failure | internal=%s",
            claim_id, action, e.internal, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=e.public,
        )
    except Exception as e:
        if e.__class__.__name__ == "ClaimStateConflictError":
            logger.warning(f"POST /claims/{claim_id}/{action} conflict: {e}")
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
        logger.error(
            f"POST /claims/{claim_id}/{action} error: {e!r}", exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error updating claim",
        )

    # Record success for idempotency + rate counting.
    # Fix D: now async + passes reason so the full key is recorded.
    await record_approval_action(user.sub, claim_id, action, row, reason=reason)

    return ClaimStatusResponse(**row)
