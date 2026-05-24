"""POST /auth/token — dev-only JWT issuance helper.

This endpoint exists so we can develop + smoke-test the orchestrator
without bringing up a full Identity Provider. In production, prod, and
staging environments this entire router is disabled (returns 404) —
tokens then come from the IdP only.

NOT to be exposed to the internet. The endpoint refuses to issue tokens
whenever ENVIRONMENT is "production", "prod", or "staging".
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_ORCH_ROOT     = Path(__file__).resolve().parents[1]
_PROJECT_ROOT  = _ORCH_ROOT.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_ORCH_ROOT))

from packages.common.schemas.api import TokenRequest, TokenResponse  # noqa: E402

from auth        import Role, create_access_token, decode_jwt  # noqa: E402
from config      import get_settings                            # noqa: E402

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])
_bearer = HTTPBearer(auto_error=False)


@router.post(
    "/token",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="(DEV-ONLY) Issue a signed JWT for testing",
    description=(
        "Returns a signed JWT for the given `sub` + `role`. This endpoint "
        "is disabled in production, prod, and staging environments "
        "(`ENVIRONMENT=production|prod|staging`)."
    ),
    responses={
        404: {"description": "Endpoint not available in production"},
        400: {"description": "Invalid role"},
    },
)
async def issue_dev_token(body: TokenRequest) -> TokenResponse:
    """Mint a JWT signed with JWT_SECRET_KEY (HS256 by default)."""
    settings = get_settings()

    # Fix #76 — block in prod, staging, AND "prod" alias (not just "production").
    _PROTECTED_ENVS = {"production", "prod", "staging"}
    if settings.environment.lower() in _PROTECTED_ENVS:
        logger.warning(
            "POST /auth/token attempted in protected environment | env=%s",
            settings.environment,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    role = Role(body.role)
    token = create_access_token(sub=body.sub, role=role)
    logger.info("Dev token issued | sub=%s role=%s", body.sub, role.value)

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        role=role.value,
        expires_in=settings.jwt_expiration_hours * 3600,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
    summary="Revoke the caller's current JWT",
    description=(
        "Adds the token's `jti` to a Redis blocklist so subsequent requests "
        "using the same token are rejected with 401. Works in all environments. "
        "Falls back gracefully when Redis is unavailable (logs a warning)."
    ),
    responses={
        401: {"description": "Missing or invalid token"},
    },
)
async def logout(
    request:     Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, str]:
    """Revoke the caller's JWT by adding its JTI to the Redis blocklist.

    Fix #69 — closes the no-revocation gap: a compromised token can now
    be invalidated immediately instead of waiting for natural expiry.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization: Bearer <token> header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verify signature and expiry (raises 401 on invalid token).
    payload = decode_jwt(credentials.credentials)
    jti     = payload.get("jti")
    sub     = payload.get("sub", "<unknown>")

    if jti:
        redis = getattr(request.app.state, "redis", None)
        if redis is not None:
            remaining_ttl = max(1, int(payload.get("exp", 0)) - int(time.time()))
            try:
                await redis.set(f"revoked_jti:{jti}", "1", ex=remaining_ttl)
                logger.info(
                    "JWT revoked | sub=%s jti=%s ttl=%ds", sub, jti, remaining_ttl,
                )
            except Exception as exc:
                logger.warning(
                    "JWT revocation failed (Redis error) | sub=%s jti=%s err=%s",
                    sub, jti, exc,
                )
        else:
            logger.warning(
                "JWT logout without Redis — token not persisted to blocklist | sub=%s",
                sub,
            )
    else:
        logger.warning(
            "JWT logout: token has no jti claim — cannot revoke | sub=%s", sub,
        )

    return {"status": "logged out"}


@router.post(
    "/revoke-user/{sub}",
    status_code=status.HTTP_200_OK,
    summary="(ADMIN) Invalidate all active tokens for a user",
    description=(
        "Records a 'revoke-before' timestamp for `sub` in Redis. "
        "Any token issued to that user before this moment will be "
        "rejected on the next request, regardless of expiry. "
        "Requires ADMIN role. Use when a user's role changes or access "
        "must be terminated immediately (Fix #89)."
    ),
    responses={
        401: {"description": "Missing or invalid token"},
        403: {"description": "Admin role required"},
        503: {"description": "Redis unavailable — revocation not persisted"},
    },
)
async def revoke_user_tokens(
    sub:         str,
    request:     Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, str]:
    """Invalidate ALL outstanding tokens for the given user subject.

    Fix #89 — closes the role-change gap: a 24h JWT stays valid even after
    an admin changes the user's role.  This endpoint sets a per-user
    `revoked_before:{sub}` key in Redis containing the current Unix
    timestamp.  get_current_user() rejects any token whose `iat` (issued-at)
    is older than this value.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization: Bearer <token> header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verify caller is an admin.
    payload = decode_jwt(credentials.credentials)
    caller_role_str = payload.get("role", "")
    try:
        caller_role = Role(caller_role_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid role claim in token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if caller_role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required to revoke user tokens",
        )

    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        logger.error(
            "revoke-user | Redis unavailable — cannot persist revocation | sub=%s", sub,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Revocation requires Redis. REDIS_URL is not configured.",
        )

    now_ts = int(time.time())
    # Keep this key for JWT_EXPIRATION_HOURS + 1 h to auto-expire safely.
    settings = get_settings()
    ttl = (settings.jwt_expiration_hours + 1) * 3600
    try:
        await redis.set(f"revoked_before:{sub}", str(now_ts), ex=ttl)
        logger.info(
            "revoke-user | all tokens revoked | sub=%s revoked_before=%d caller=%s",
            sub, now_ts, payload.get("sub"),
        )
    except Exception as exc:
        logger.error("revoke-user | Redis error | sub=%s err=%s", sub, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to persist revocation — try again",
        )

    return {"status": "revoked", "sub": sub, "revoked_before": str(now_ts)}
