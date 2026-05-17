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

from fastapi import APIRouter, Response, status

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
async def ready(response: Response) -> ReadinessResponse:
    """Readiness — dependencies are healthy. K8s routes traffic only if ok."""
    settings = get_settings()
    checks:  dict[str, bool] = {}
    details: dict[str, str]  = {}

    # LLM provider config
    checks["llm_provider_api_key"] = bool(settings.llm_provider_api_key)
    if not checks["llm_provider_api_key"]:
        details["llm_provider_api_key"] = "LLM_PROVIDER_API_KEY is empty"

    checks["model_name"] = bool(settings.model_name)
    if not checks["model_name"]:
        details["model_name"] = "MODEL_NAME is empty"

    # DB url (orchestrator never queries DB directly per Rule 2, but its
    # MCPs need this — failing fast on missing config is healthier than
    # crashing on first claim)
    checks["database_url"] = bool(settings.database_url)
    if not checks["database_url"]:
        details["database_url"] = "DATABASE_URL is empty"

    is_ready = all(checks.values())
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        logger.warning(f"Readiness check FAILED | checks={checks} details={details}")

    return ReadinessResponse(ready=is_ready, checks=checks, details=details)
