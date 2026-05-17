"""POST /auth/token — dev-only JWT issuance helper.

This endpoint exists so we can develop + smoke-test the orchestrator
without bringing up a full Identity Provider. In production this entire
router is disabled (returns 404) — tokens then come from the IdP only.

NOT to be exposed to the internet. If you accidentally deploy this with
ENVIRONMENT=production, the endpoint refuses to issue tokens.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, status

_ORCH_ROOT     = Path(__file__).resolve().parents[1]
_PROJECT_ROOT  = _ORCH_ROOT.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_ORCH_ROOT))

from packages.common.schemas.api import TokenRequest, TokenResponse  # noqa: E402

from auth   import Role, create_access_token  # noqa: E402
from config import get_settings                # noqa: E402

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/token",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="(DEV-ONLY) Issue a signed JWT for testing",
    description=(
        "Returns a signed JWT for the given `sub` + `role`. This endpoint "
        "is disabled in production (`ENVIRONMENT=production`)."
    ),
    responses={
        404: {"description": "Endpoint not available in production"},
        400: {"description": "Invalid role"},
    },
)
async def issue_dev_token(body: TokenRequest) -> TokenResponse:
    """Mint a JWT signed with JWT_SECRET_KEY (HS256 by default)."""
    settings = get_settings()

    if settings.environment.lower() == "production":
        logger.warning("POST /auth/token attempted in production")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    role = Role(body.role)
    token = create_access_token(sub=body.sub, role=role)
    logger.info(f"Dev token issued | sub={body.sub} role={role.value}")

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        role=role.value,
        expires_in=settings.jwt_expiration_hours * 3600,
    )
