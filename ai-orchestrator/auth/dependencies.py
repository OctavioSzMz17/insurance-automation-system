"""FastAPI dependencies that enforce JWT auth + role-based access (Rule 7).

Usage in a route:

    from auth import require_role, Role

    @router.post("/claims/{id}/approve")
    async def approve(..., user = Depends(require_role(Role.AUDITOR))):
        # `user` is a UserPrincipal — guaranteed valid + role-checked
        ...

If the request is missing a token → 401.
If the token is bad / expired      → 401.
If the role is too low             → 403.
"""

from __future__ import annotations

from typing import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .jwt   import decode_jwt
from .roles import Role, UserPrincipal, has_role_or_higher


# HTTPBearer auto-extracts the token from "Authorization: Bearer <token>".
# auto_error=True → missing header returns 403 by default; we override to 401
# inside the dependency so the error semantics match RFC 6750.
_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    request:     Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> UserPrincipal:
    """Resolve the request's authenticated user from its bearer token.

    Fix #69 — after signature verification, checks the token's `jti`
    against the Redis JTI revocation set. A revoked token is rejected
    with 401 even if the signature is valid and the token has not expired.
    Falls back gracefully when Redis is unavailable (logs a warning, does
    not block the request — fail-open so a Redis outage doesn't lock out
    all users).
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization: Bearer <token> header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_jwt(credentials.credentials)

    redis = getattr(request.app.state, "redis", None)

    # Fix #69 — JTI revocation check via Redis blocklist.
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
            import logging
            logging.getLogger(__name__).warning(
                "JTI revocation check failed (Redis error) — fail-open | jti=%s err=%s",
                jti, exc,
            )
    # Fix #89 — per-user revocation check.
    # When an admin calls POST /auth/revoke-user/{sub}, a `revoked_before:{sub}`
    # key is written to Redis.  Any token whose `iat` < that timestamp is
    # rejected.  Fail-open when Redis is unavailable (same policy as JTI check).
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
            import logging
            logging.getLogger(__name__).warning(
                "Per-user revocation check failed (Redis error) — fail-open | sub=%s err=%s",
                token_sub, exc,
            )

    sub      = payload.get("sub")
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
    """Factory: returns a FastAPI dependency that enforces `min_role`.

    Args:
        min_role: Minimum tier required to access the endpoint.

    Returns:
        An async dependency function for use with Depends().
    """

    async def _checker(
        user: UserPrincipal = Depends(get_current_user),
    ) -> UserPrincipal:
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
