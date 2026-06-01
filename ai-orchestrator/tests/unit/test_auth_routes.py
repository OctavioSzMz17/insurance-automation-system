"""Unit tests for routes/auth.py — /auth/token, /auth/logout, /auth/revoke-user."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.auth import router


# ── Minimal test app ──────────────────────────────────────────────────────────

def _make_app(env: str = "development", redis=None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    @app.on_event("startup")
    async def _setup():
        app.state.redis = redis

    return app


@pytest.fixture
def dev_client():
    class S:
        environment = "development"
        jwt_secret_key = "x" * 32
        jwt_algorithm = "HS256"
        jwt_expiration_hours = 1
        jwt_audience = ""
        jwt_issuer = ""

    with patch("routes.auth.get_settings", return_value=S()):
        with patch("auth.jwt.get_settings", return_value=S()):
            app = _make_app(env="development")
            yield TestClient(app)


@pytest.fixture
def prod_client():
    class S:
        environment = "production"
        jwt_secret_key = "x" * 32
        jwt_algorithm = "HS256"
        jwt_expiration_hours = 1
        jwt_audience = ""
        jwt_issuer = ""

    with patch("routes.auth.get_settings", return_value=S()):
        with patch("auth.jwt.get_settings", return_value=S()):
            app = _make_app(env="production")
            yield TestClient(app)


# ── /auth/token ───────────────────────────────────────────────────────────────

def test_issue_dev_token_success(dev_client):
    resp = dev_client.post("/auth/token", json={"sub": "alice", "role": "analyst"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert "access_token" in body
    assert body["role"] == "analyst"
    assert body["expires_in"] == 3600


def test_issue_dev_token_invalid_role(dev_client):
    resp = dev_client.post("/auth/token", json={"sub": "alice", "role": "superuser"})
    assert resp.status_code == 400


def test_issue_dev_token_disabled_in_prod(prod_client):
    resp = prod_client.post("/auth/token", json={"sub": "alice", "role": "analyst"})
    assert resp.status_code == 404


def test_issue_dev_token_roles(dev_client):
    for role in ("analyst", "auditor", "admin"):
        resp = dev_client.post("/auth/token", json={"sub": f"user-{role}", "role": role})
        assert resp.status_code == 200
        assert resp.json()["role"] == role


# ── /auth/logout ──────────────────────────────────────────────────────────────

def test_logout_without_token_returns_401(dev_client):
    resp = dev_client.post("/auth/logout")
    assert resp.status_code == 401


def test_logout_revokes_jti(dev_client):
    token_resp = dev_client.post("/auth/token", json={"sub": "bob", "role": "analyst"})
    token = token_resp.json()["access_token"]

    redis_mock = AsyncMock()
    redis_mock.set = AsyncMock(return_value=True)

    class S:
        environment = "development"
        jwt_secret_key = "x" * 32
        jwt_algorithm = "HS256"
        jwt_expiration_hours = 1
        jwt_audience = ""
        jwt_issuer = ""

    with patch("routes.auth.get_settings", return_value=S()):
        with patch("auth.jwt.get_settings", return_value=S()):
            app = _make_app(redis=redis_mock)
            with TestClient(app) as c:
                resp = c.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "logged out"
    redis_mock.set.assert_called_once()
    key = redis_mock.set.call_args[0][0]
    assert key.startswith("revoked_jti:")


def test_logout_no_redis_still_returns_200(dev_client):
    token_resp = dev_client.post("/auth/token", json={"sub": "carol", "role": "analyst"})
    token = token_resp.json()["access_token"]
    resp = dev_client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
