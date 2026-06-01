"""FastAPI dependencies that enforce JWT auth + role-based access (Rule 7).

Usage:
    from auth import require_role, Role

    @router.post("/endpoint")
    async def handler(user: UserPrincipal = Depends(require_role(Role.AUDITOR))):
        ...
"""
from __future__ import annotations

import logging
from typing import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .jwt import decode_jwt
from .roles import Role, UserPrincipal, has_role_or_higher

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> UserPrincipal:
    """Resolve the authenticated user from the bearer token.

    Checks the JTI against a Redis blocklist after signature verification.
    Fail-open when Redis is unavailable (logs warning, does not block request).
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization: Bearer <token> header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_jwt(credentials.credentials)
    redis = getattr(request.app.state, "redis", None)

    # JTI revocation check
    jti = payload.get("jti")
    if jti and redis is not None:
        try:
            revoked = await redis.get(f"revoked_jti:{jti}")
            if revoked:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token has been revoked",
                    headers={"WWW-Authenticate": "Bearer"},
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("JTI revocation check failed (Redis error) — fail-open | jti=%s err=%s", jti, exc)

    # Per-user revocation check
    token_sub = payload.get("sub", "")
    token_iat = payload.get("iat", 0)
    if token_sub and redis is not None:
        try:
            revoked_before_raw = await redis.get(f"revoked_before:{token_sub}")
            if revoked_before_raw is not None:
                revoked_before_ts = int(revoked_before_raw)
                if token_iat < revoked_before_ts:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Token has been revoked due to a role or access change",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("Per-user revocation check failed — fail-open | sub=%s err=%s", token_sub, exc)

    sub = payload.get("sub")
    role_str = payload.get("role")
    if not sub or not role_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing required claims (sub / role)",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        role = Role(role_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Unknown role in token: {role_str!r}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return UserPrincipal(sub=sub, role=role)


def require_role(min_role: Role) -> Callable[..., UserPrincipal]:
    """Factory that returns a FastAPI dependency enforcing min_role."""

    async def _checker(user: UserPrincipal = Depends(get_current_user)) -> UserPrincipal:
        if not has_role_or_higher(user.role, min_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role {user.role.value!r} cannot access this endpoint "
                    f"(requires {min_role.value!r} or higher)"
                ),
            )
        return user

    return _checker
