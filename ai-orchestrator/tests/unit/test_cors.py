"""Unit tests for CORS configuration — verifies strict origin allowlist."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient


def _make_app(allowed_origins: list[str]) -> FastAPI:
    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        yield

    app = FastAPI(lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    return app


_ALLOWED = "http://localhost:3000"
_DISALLOWED = "https://evil.example.com"


def test_allowed_origin_gets_acao_header():
    app = _make_app([_ALLOWED])
    with TestClient(app) as c:
        resp = c.get("/ping", headers={"Origin": _ALLOWED})
    assert resp.headers.get("access-control-allow-origin") == _ALLOWED


def test_disallowed_origin_no_acao_header():
    app = _make_app([_ALLOWED])
    with TestClient(app) as c:
        resp = c.get("/ping", headers={"Origin": _DISALLOWED})
    assert "access-control-allow-origin" not in resp.headers


def test_preflight_allowed_origin():
    app = _make_app([_ALLOWED])
    with TestClient(app) as c:
        resp = c.options(
            "/ping",
            headers={
                "Origin": _ALLOWED,
                "Access-Control-Request-Method": "POST",
            },
        )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == _ALLOWED


def test_preflight_disallowed_origin_rejected():
    app = _make_app([_ALLOWED])
    with TestClient(app) as c:
        resp = c.options(
            "/ping",
            headers={
                "Origin": _DISALLOWED,
                "Access-Control-Request-Method": "POST",
            },
        )
    assert "access-control-allow-origin" not in resp.headers


def test_wildcard_never_set():
    """The ACAO header must never be * — strict allowlist only."""
    app = _make_app([_ALLOWED, "https://app.insureai.io"])
    with TestClient(app) as c:
        for origin in [_ALLOWED, "https://app.insureai.io"]:
            resp = c.get("/ping", headers={"Origin": origin})
            acao = resp.headers.get("access-control-allow-origin", "")
            assert acao != "*", f"Wildcard CORS detected for origin {origin}"
            assert acao == origin
