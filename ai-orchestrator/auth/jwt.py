"""JWT signing + verification (Rule 7).

Uses python-jose HS256. All secrets come from OrchestratorSettings.
create_access_token() is dev/test only — production uses an IdP.
decode_jwt() is used in all environments.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from jose import JWTError, jwt

from config import get_settings
from .roles import Role

logger = logging.getLogger(__name__)


def create_access_token(sub: str, role: Role, expires_hours: int | None = None) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    ttl = expires_hours if expires_hours is not None else settings.jwt_expiration_hours

    payload: dict[str, Any] = {
        "sub": sub,
        "role": role.value,
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=ttl)).timestamp()),
    }
    if settings.jwt_audience:
        payload["aud"] = settings.jwt_audience
    if settings.jwt_issuer:
        payload["iss"] = settings.jwt_issuer

    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_jwt(token: str) -> dict[str, Any]:
    settings = get_settings()
    audience = settings.jwt_audience or None
    issuer = settings.jwt_issuer or None
    try:
        return jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            audience=audience,
            issuer=issuer,
        )
    except JWTError as exc:
        logger.debug("decode_jwt failed | reason=%s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
