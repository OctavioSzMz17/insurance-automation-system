"""Unit tests for auth/jwt.py — create_access_token + decode_jwt."""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from auth.jwt import create_access_token, decode_jwt
from auth.roles import Role


@pytest.fixture
def _fake_settings():
    class S:
        jwt_secret_key = "x" * 32
        jwt_algorithm = "HS256"
        jwt_expiration_hours = 1
        jwt_audience = ""
        jwt_issuer = ""

    with patch("auth.jwt.get_settings", return_value=S()):
        yield S()


def test_create_token_contains_expected_claims(_fake_settings):
    token = create_access_token(sub="alice", role=Role.ANALYST)
    payload = decode_jwt(token)
    assert payload["sub"] == "alice"
    assert payload["role"] == "analyst"
    assert "jti" in payload
    assert "iat" in payload
    assert "exp" in payload


def test_create_token_custom_ttl(_fake_settings):
    token = create_access_token(sub="bob", role=Role.ADMIN, expires_hours=2)
    payload = decode_jwt(token)
    remaining = payload["exp"] - int(time.time())
    assert 7000 < remaining <= 7200


def test_create_token_with_aud_iss():
    class S:
        jwt_secret_key = "x" * 32
        jwt_algorithm = "HS256"
        jwt_expiration_hours = 1
        jwt_audience = "insurance-api"
        jwt_issuer = "auth.internal"

    with patch("auth.jwt.get_settings", return_value=S()):
        token = create_access_token(sub="carol", role=Role.AUDITOR)
        payload = decode_jwt(token)
        assert payload["aud"] == "insurance-api"
        assert payload["iss"] == "auth.internal"


def test_decode_jwt_wrong_signature_raises_401(_fake_settings):
    token = create_access_token(sub="eve", role=Role.ANALYST)
    tampered = token[:-4] + "XXXX"
    with pytest.raises(HTTPException) as exc_info:
        decode_jwt(tampered)
    assert exc_info.value.status_code == 401


def test_decode_jwt_expired_raises_401():
    class S:
        jwt_secret_key = "x" * 32
        jwt_algorithm = "HS256"
        jwt_expiration_hours = 1
        jwt_audience = ""
        jwt_issuer = ""

    with patch("auth.jwt.get_settings", return_value=S()):
        token = create_access_token(sub="mallory", role=Role.ANALYST, expires_hours=-1)
    with patch("auth.jwt.get_settings", return_value=S()):
        with pytest.raises(HTTPException) as exc_info:
            decode_jwt(token)
        assert exc_info.value.status_code == 401
        # Must not leak internal jose error details (Rule 8 / Fix #83).
        assert "invalid" in exc_info.value.detail.lower() or "expired" in exc_info.value.detail.lower()


def test_decode_jwt_error_detail_is_generic(_fake_settings):
    with pytest.raises(HTTPException) as exc_info:
        decode_jwt("not.a.jwt")
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid or expired token."
