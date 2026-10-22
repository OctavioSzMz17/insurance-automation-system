"""Unit tests for routes/sse.py — POST /sse/ticket and GET /sse."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.roles import Role
from auth.stream_tickets import StreamTicketStore
from routes.sse import router

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_token(sub: str = "alice", role: str = "analyst") -> str:
    class S:
        jwt_secret_key = "x" * 32
        jwt_algorithm = "HS256"
        jwt_expiration_hours = 1
        jwt_audience = ""
        jwt_issuer = ""

    from auth.jwt import create_access_token
    with patch("auth.jwt.get_settings", return_value=S()):
        return create_access_token(sub=sub, role=Role(role))


def _make_app(ticket_store: StreamTicketStore | None = None) -> FastAPI:
    store = ticket_store or StreamTicketStore()

    class S:
        jwt_secret_key = "x" * 32
        jwt_algorithm = "HS256"
        jwt_expiration_hours = 1
        jwt_audience = ""
        jwt_issuer = ""

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        app.state.ticket_store = store
        app.state.redis = None
        yield

    app = FastAPI(lifespan=_lifespan)
    app.include_router(router)
    return app, S()


# ── POST /sse/ticket ──────────────────────────────────────────────────────────

def test_mint_ticket_returns_ticket_id():
    app, s = _make_app()
    token = _make_token()
    with patch("auth.jwt.get_settings", return_value=s):
        with TestClient(app) as c:
            resp = c.post("/sse/ticket", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 201
    body = resp.json()
    assert "ticket_id" in body
    assert "request_id" in body
    assert "expires_in" in body
    assert body["expires_in"] == 30


def test_mint_ticket_requires_auth():
    app, s = _make_app()
    with patch("auth.jwt.get_settings", return_value=s):
        with TestClient(app) as c:
            resp = c.post("/sse/ticket")
    assert resp.status_code == 401


def test_mint_ticket_returns_unique_ids():
    app, s = _make_app()
    token = _make_token()
    ids = []
    with patch("auth.jwt.get_settings", return_value=s):
        with TestClient(app) as c:
            for _ in range(3):
                resp = c.post("/sse/ticket", headers={"Authorization": f"Bearer {token}"})
                assert resp.status_code == 201
                ids.append(resp.json()["ticket_id"])
    assert len(set(ids)) == 3


def test_mint_ticket_fails_when_store_missing():
    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        app.state.ticket_store = None   # simulates uninitialised store
        app.state.redis = None
        yield

    class S:
        jwt_secret_key = "x" * 32
        jwt_algorithm = "HS256"
        jwt_expiration_hours = 1
        jwt_audience = ""
        jwt_issuer = ""

    app = FastAPI(lifespan=_lifespan)
    app.include_router(router)
    token = _make_token()
    s = S()
    with patch("auth.jwt.get_settings", return_value=s):
        with TestClient(app) as c:
            resp = c.post("/sse/ticket", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 503


# ── GET /sse?ticket={id} ──────────────────────────────────────────────────────

def test_sse_invalid_ticket_returns_401():
    app, s = _make_app()
    with patch("auth.jwt.get_settings", return_value=s):
        with TestClient(app) as c:
            resp = c.get("/sse?ticket=does-not-exist")
    assert resp.status_code == 401


def test_sse_valid_ticket_streams_connected_event():
    """Valid ticket → 200, text/event-stream, first data block is 'connected'."""
    store = StreamTicketStore()
    app, s = _make_app(ticket_store=store)
    token = _make_token()

    # Patch _event_stream to emit only the connected event then stop.

    from routes import sse as sse_mod

    async def _finite_stream(request, sub, role, request_id, **kwargs):
        import json as _json
        payload = _json.dumps({"sub": sub, "role": role, "request_id": request_id})
        yield f"event: connected\ndata: {payload}\n\n"

    with patch("auth.jwt.get_settings", return_value=s):
        with patch.object(sse_mod, "_event_stream", new=_finite_stream):
            with TestClient(app) as c:
                mint_resp = c.post("/sse/ticket", headers={"Authorization": f"Bearer {token}"})
                assert mint_resp.status_code == 201
                ticket_id = mint_resp.json()["ticket_id"]

                resp = c.get(f"/sse?ticket={ticket_id}")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")
    assert "event: connected" in resp.text


def test_sse_ticket_one_time_use():
    """Second connection attempt with the same ticket is rejected."""
    store = StreamTicketStore()
    app, s = _make_app(ticket_store=store)
    token = _make_token()

    from routes import sse as sse_mod

    async def _finite_stream(request, sub, role, request_id, **kwargs):
        import json as _json
        yield f"event: connected\ndata: {_json.dumps({'sub': sub})}\n\n"

    with patch("auth.jwt.get_settings", return_value=s):
        with patch.object(sse_mod, "_event_stream", new=_finite_stream):
            with TestClient(app) as c:
                mint_resp = c.post("/sse/ticket", headers={"Authorization": f"Bearer {token}"})
                ticket_id = mint_resp.json()["ticket_id"]

                # First use — OK
                resp1 = c.get(f"/sse?ticket={ticket_id}")
                assert resp1.status_code == 200

                # Second use — rejected (ticket already consumed)
                resp2 = c.get(f"/sse?ticket={ticket_id}")
    assert resp2.status_code == 401


def test_sse_missing_ticket_param_returns_422():
    app, s = _make_app()
    with patch("auth.jwt.get_settings", return_value=s):
        with TestClient(app) as c:
            resp = c.get("/sse")
    assert resp.status_code == 422
