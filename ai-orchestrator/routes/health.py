"""Health + Readiness endpoints — Phase C C1.1.

`GET /health`  — Liveness probe. Returns 200 as long as the process is up.
`GET /ready`   — Readiness probe. Verifies dependencies are reachable
                 (DB url present, LLM provider configured). Returns 503
                 if anything required is missing.

Used by Kubernetes livenessProbe / readinessProbe directly.

Rules applied:
    Rule 8  — All config from .env (via Settings)
    Rule 11 — Pydantic responses (HealthResponse / ReadinessResponse)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Request, Response, status

_ORCH_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = _ORCH_ROOT.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_ORCH_ROOT))

from packages.common.schemas.api import HealthResponse, ReadinessResponse  # noqa: E402
from config import get_settings                                             # noqa: E402

logger = logging.getLogger(__name__)
router  = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    """Liveness — the process is alive. K8s restarts the pod if this fails."""
    settings = get_settings()
    return HealthResponse(environment=settings.environment)


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={503: {"description": "One or more dependencies are not ready"}},
)
async def ready(request: Request, response: Response) -> ReadinessResponse:
    """Readiness — dependencies are healthy. K8s routes traffic only if ok.

    Fix #75 — now performs real connectivity checks instead of only
    verifying that config strings are non-empty:
      - LLM config: unchanged (config presence check)
      - Redis:       actual PING (fast, ~1ms); skipped if Redis not configured
      - AgentRouter: initialised on app.state (startup guard)
      - MCPRouter:   initialised on app.state (startup guard)
    """
    settings = get_settings()
    checks:  dict[str, bool] = {}
    details: dict[str, str]  = {}

    # ── 1. LLM provider config (config-presence check) ───────────
    checks["llm_provider_api_key"] = bool(settings.llm_provider_api_key)
    if not checks["llm_provider_api_key"]:
        details["llm_provider_api_key"] = "LLM_PROVIDER_API_KEY is empty"

    checks["model_name"] = bool(settings.model_name)
    if not checks["model_name"]:
        details["model_name"] = "MODEL_NAME is empty"

    checks["database_url"] = bool(settings.database_url)
    if not checks["database_url"]:
        details["database_url"] = "DATABASE_URL is empty"

    # ── 2. Redis — actual PING (skipped when Redis is not configured) ─
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            await redis.ping()
            checks["redis"] = True
        except Exception as exc:
            checks["redis"] = False
            details["redis"] = f"Redis PING failed: {exc}"
            logger.warning("Readiness: Redis ping failed | err=%s", exc)
    else:
        # Redis not configured — in-memory mode is valid for single-replica dev.
        details["redis"] = "Redis not configured (in-memory fallback active)"

    # ── 3. AgentRouter — must be initialised at startup ──────────
    agent_router = getattr(request.app.state, "agent_router", None)
    checks["agent_router"] = agent_router is not None
    if not checks["agent_router"]:
        details["agent_router"] = "AgentRouter failed to initialise at startup"

    # ── 4. MCPRouter — must be initialised at startup ─────────────
    mcp_router = getattr(request.app.state, "mcp_router", None)
    checks["mcp_router"] = mcp_router is not None
    if not checks["mcp_router"]:
        details["mcp_router"] = "MCPRouter failed to initialise at startup"

    is_ready = all(checks.values())
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        logger.warning("Readiness check FAILED | checks=%s details=%s", checks, details)

    # Fix #94 — /ready is publicly reachable (K8s probes carry no auth header).
    # Returning internal `details` (e.g. "Redis PING failed: <host>:<port>",
    # "LLM_PROVIDER_API_KEY is empty") leaks infrastructure topology to any
    # unauthenticated caller.  All detail is already logged; the response body
    # only exposes boolean check flags so the probe status is still actionable.
    return ReadinessResponse(ready=is_ready, checks=checks, details={})
