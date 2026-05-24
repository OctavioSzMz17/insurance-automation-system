"""JWT signing + verification.

Uses python-jose with HS256 by default. Secret + algorithm + expiry
all come from Settings (.env).

In production the orchestrator would NOT issue its own tokens — an IdP
(Auth0, Cognito, Keycloak) would. We only need decode_jwt() in prod.
The create_access_token() helper exists for tests and the dev-only
POST /auth/token endpoint (Rule 7 / Rule 8).
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from jose import JWTError, jwt

# Allow stand-alone imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import get_settings  # noqa: E402

from .roles import Role


def create_access_token(
    sub:           str,
    role:          Role,
    expires_hours: int | None = None,
) -> str:
    """Build and sign a JWT for the given subject / role.

    Args:
        sub:           User identifier (e.g. "octav.admin"). Maps to JWT `sub`.
        role:          One of Role.{ANALYST, AUDITOR, ADMIN}.
        expires_hours: Lifetime in hours; defaults to JWT_EXPIRATION_HOURS.

    Returns:
        The signed JWT as a string.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    ttl = expires_hours if expires_hours is not None else settings.jwt_expiration_hours

    payload: dict[str, Any] = {
        "sub":  sub,
        "role": role.value,
        "jti":  str(uuid.uuid4()),   # Fix #69 — unique token ID for revocation
        "iat":  int(now.timestamp()),
        "exp":  int((now + timedelta(hours=ttl)).timestamp()),
    }
    return jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def decode_jwt(token: str) -> dict[str, Any]:
    """Verify and decode a JWT. Raises 401 on any failure.

    Fix #65 — validates `aud` and `iss` claims when JWT_AUDIENCE /
    JWT_ISSUER are configured in settings. Passing `audience` to
    python-jose causes it to reject tokens whose `aud` doesn't match,
    closing the cross-service token reuse vector.

    Args:
        token: The raw JWT string (without "Bearer ").

    Returns:
        The decoded payload dict.

    Raises:
        HTTPException(401): If the token is missing, expired, tampered,
                            or signed with the wrong key / wrong aud/iss.
    """
    settings = get_settings()
    options: dict[str, Any] = {}
    audience = settings.jwt_audience or None
    issuer   = settings.jwt_issuer   or None
    try:
        return jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            audience=audience,
            issuer=issuer,
            options=options,
        )
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )
