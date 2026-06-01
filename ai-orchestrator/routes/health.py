"""Liveness and readiness probes — K8s compatible (Rule 10).

GET /health  — liveness: process is alive.
GET /ready   — readiness: dependencies are healthy.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response, status

from config import get_settings
from packages.common.schemas.api import HealthResponse, ReadinessResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(environment=settings.environment)


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={503: {"description": "One or more dependencies not ready"}},
)
async def ready(request: Request, response: Response) -> ReadinessResponse:
    """Check that all required runtime dependencies are healthy."""
    settings = get_settings()
    checks: dict[str, bool] = {}
    details: dict[str, str] = {}

    # ── Redis (actual PING when configured) ──────────────────────────────────
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            await redis.ping()
            checks["redis"] = True
        except Exception as exc:
            checks["redis"] = False
            details["redis"] = f"Redis ping failed: {exc}"
            logger.warning("Readiness: Redis ping failed | err=%s", exc)
    else:
        checks["redis"] = bool(settings.redis_url)
        if not checks["redis"]:
            details["redis"] = "REDIS_URL not configured"

    # ── MCP pool ─────────────────────────────────────────────────────────────
    pool = getattr(request.app.state, "pool", None)
    checks["mcp_pool"] = pool is not None
    if not checks["mcp_pool"]:
        details["mcp_pool"] = "MCPClientPool not initialised"

    # ── Analyst + Auditor ─────────────────────────────────────────────────────
    checks["analyst"] = getattr(request.app.state, "analyst", None) is not None
    checks["auditor"] = getattr(request.app.state, "auditor", None) is not None

    is_ready = all(checks.values())
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        logger.warning("Readiness FAILED | checks=%s", checks)

    # Never leak internal detail strings to unauthenticated callers.
    return ReadinessResponse(ready=is_ready, checks=checks, details={})
