"""Auth routes.

POST /auth/token         — (DEV-ONLY) issue a signed JWT. Disabled in prod/staging.
POST /auth/logout        — revoke the caller's current JWT via JTI blocklist.
POST /auth/revoke-user/{sub} — (ADMIN) invalidate all tokens for a user.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth import Role, create_access_token, decode_jwt
from config import get_settings
from packages.common.schemas.api import TokenRequest, TokenResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])
_bearer = HTTPBearer(auto_error=False)

_PROTECTED_ENVS = {"production", "prod", "staging"}


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="(DEV-ONLY) Issue a signed JWT for testing",
    responses={404: {"description": "Endpoint not available in production"}},
)
async def issue_dev_token(body: TokenRequest) -> TokenResponse:
    """Mint a JWT for dev/test. Disabled when ENVIRONMENT is prod/staging."""
    settings = get_settings()
    if settings.environment.lower() in _PROTECTED_ENVS:
        logger.warning("POST /auth/token attempted in protected env | env=%s", settings.environment)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    try:
        role = Role(body.role)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown role {body.role!r}. Valid: analyst, auditor, admin",
        )

    token = create_access_token(sub=body.sub, role=role)
    logger.info("Dev token issued | sub=%s role=%s", body.sub, role.value)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        role=role.value,
        expires_in=settings.jwt_expiration_hours * 3600,
    )


@router.post("/logout", status_code=status.HTTP_200_OK, summary="Revoke caller's JWT")
async def logout(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, str]:
    """Add the token's JTI to the Redis blocklist."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization: Bearer <token> header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_jwt(credentials.credentials)
    jti = payload.get("jti")
    sub = payload.get("sub", "<unknown>")

    if jti:
        redis = getattr(request.app.state, "redis", None)
        if redis is not None:
            remaining_ttl = max(1, int(payload.get("exp", 0)) - int(time.time()))
            try:
                await redis.set(f"revoked_jti:{jti}", "1", ex=remaining_ttl)
                logger.info("JWT revoked | sub=%s jti=%s ttl=%ds", sub, jti, remaining_ttl)
            except Exception as exc:
                logger.warning("JWT revocation failed (Redis error) | sub=%s err=%s", sub, exc)
        else:
            logger.warning("JWT logout without Redis — token not in blocklist | sub=%s", sub)

    return {"status": "logged out"}


@router.post(
    "/revoke-user/{sub}",
    status_code=status.HTTP_200_OK,
    summary="(ADMIN) Invalidate all active tokens for a user",
    responses={
        403: {"description": "Admin role required"},
        503: {"description": "Redis unavailable"},
    },
)
async def revoke_user_tokens(
    sub: str,
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, str]:
    """Set a revoke-before timestamp for sub — rejects all tokens issued before now."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization: Bearer <token> header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_jwt(credentials.credentials)
    caller_role_str = payload.get("role", "")
    try:
        caller_role = Role(caller_role_str)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid role claim")

    if caller_role != Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")

    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Revocation requires Redis. REDIS_URL is not configured.",
        )

    settings = get_settings()
    now_ts = int(time.time())
    ttl = (settings.jwt_expiration_hours + 1) * 3600
    try:
        await redis.set(f"revoked_before:{sub}", str(now_ts), ex=ttl)
        logger.info("revoke-user | sub=%s revoked_before=%d caller=%s", sub, now_ts, payload.get("sub"))
    except Exception as exc:
        logger.error("revoke-user | Redis error | sub=%s err=%s", sub, exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Failed to persist revocation")

    return {"status": "revoked", "sub": sub, "revoked_before": str(now_ts)}
