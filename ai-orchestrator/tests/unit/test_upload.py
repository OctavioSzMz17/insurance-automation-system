"""Unit tests for routes/upload.py — POST /submit-upload."""
from __future__ import annotations

import io
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.upload import router


# ── Helpers ───────────────────────────────────────────────────────────────────

_VALID_PDF = b"%PDF-1.4 fake content here"
_NOT_PDF = b"PK\x03\x04 zip file pretending to be pdf"


def _make_token(sub: str = "alice", role: str = "analyst") -> str:
    from unittest.mock import patch as _patch

    class S:
        jwt_secret_key = "x" * 32
        jwt_algorithm = "HS256"
        jwt_expiration_hours = 1
        jwt_audience = ""
        jwt_issuer = ""

    from auth.jwt import create_access_token
    from auth.roles import Role

    with _patch("auth.jwt.get_settings", return_value=S()):
        return create_access_token(sub=sub, role=Role(role))


def _make_app(tmp_path: Path, max_bytes: int = 10 * 1024 * 1024) -> FastAPI:
    class S:
        jwt_secret_key = "x" * 32
        jwt_algorithm = "HS256"
        jwt_expiration_hours = 1
        jwt_audience = ""
        jwt_issuer = ""
        claim_upload_dir = str(tmp_path)
        max_upload_bytes = max_bytes

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        app.state.redis = None
        yield

    app = FastAPI(lifespan=_lifespan)
    app.include_router(router)

    with patch("routes.upload.get_settings", return_value=S()):
        with patch("auth.jwt.get_settings", return_value=S()):
            with patch("auth.dependencies.decode_jwt") as mock_decode:
                mock_decode.side_effect = lambda t: {
                    "sub": "alice", "role": "analyst", "jti": "test-jti",
                    "iat": 9999999999, "exp": 9999999999,
                }
                yield TestClient(app)


# ── Tests ─────────────────────────────────────────────────────────────────────

class S:
    jwt_secret_key = "x" * 32
    jwt_algorithm = "HS256"
    jwt_expiration_hours = 1
    jwt_audience = ""
    jwt_issuer = ""


def test_valid_pdf_upload(tmp_path):
    token = _make_token()

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        app.state.redis = None
        yield

    app = FastAPI(lifespan=_lifespan)
    app.include_router(router)

    s = MagicMock()
    s.claim_upload_dir = str(tmp_path)
    s.max_upload_bytes = 10 * 1024 * 1024
    s.jwt_secret_key = "x" * 32
    s.jwt_algorithm = "HS256"
    s.jwt_expiration_hours = 1
    s.jwt_audience = ""
    s.jwt_issuer = ""

    with patch("routes.upload.get_settings", return_value=s):
        with patch("auth.jwt.get_settings", return_value=s):
            with TestClient(app) as c:
                resp = c.post(
                    "/submit-upload",
                    files={"file": ("claim.pdf", io.BytesIO(_VALID_PDF), "application/pdf")},
                    headers={"Authorization": f"Bearer {token}"},
                )

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "uploaded"
    assert "pipeline_id" in body
    assert body["file_size"] == len(_VALID_PDF)
    assert len(body["sha256"]) == 64
    # File must exist on disk
    saved = tmp_path / f"{body['pipeline_id']}.pdf"
    assert saved.exists()
    assert saved.read_bytes() == _VALID_PDF


def test_non_pdf_magic_bytes_rejected(tmp_path):
    token = _make_token()

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        app.state.redis = None
        yield

    app = FastAPI(lifespan=_lifespan)
    app.include_router(router)

    s = MagicMock()
    s.claim_upload_dir = str(tmp_path)
    s.max_upload_bytes = 10 * 1024 * 1024
    s.jwt_secret_key = "x" * 32
    s.jwt_algorithm = "HS256"
    s.jwt_expiration_hours = 1
    s.jwt_audience = ""
    s.jwt_issuer = ""

    with patch("routes.upload.get_settings", return_value=s):
        with patch("auth.jwt.get_settings", return_value=s):
            with TestClient(app) as c:
                resp = c.post(
                    "/submit-upload",
                    files={"file": ("claim.pdf", io.BytesIO(_NOT_PDF), "application/pdf")},
                    headers={"Authorization": f"Bearer {token}"},
                )

    assert resp.status_code == 415
    assert "PDF" in resp.json()["detail"]


def test_file_too_large_rejected(tmp_path):
    token = _make_token()

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        app.state.redis = None
        yield

    app = FastAPI(lifespan=_lifespan)
    app.include_router(router)

    s = MagicMock()
    s.claim_upload_dir = str(tmp_path)
    s.max_upload_bytes = 10        # tiny limit for test
    s.jwt_secret_key = "x" * 32
    s.jwt_algorithm = "HS256"
    s.jwt_expiration_hours = 1
    s.jwt_audience = ""
    s.jwt_issuer = ""

    with patch("routes.upload.get_settings", return_value=s):
        with patch("auth.jwt.get_settings", return_value=s):
            with TestClient(app) as c:
                resp = c.post(
                    "/submit-upload",
                    files={"file": ("claim.pdf", io.BytesIO(_VALID_PDF), "application/pdf")},
                    headers={"Authorization": f"Bearer {token}"},
                )

    assert resp.status_code == 413


def test_empty_file_rejected(tmp_path):
    token = _make_token()

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        app.state.redis = None
        yield

    app = FastAPI(lifespan=_lifespan)
    app.include_router(router)

    s = MagicMock()
    s.claim_upload_dir = str(tmp_path)
    s.max_upload_bytes = 10 * 1024 * 1024
    s.jwt_secret_key = "x" * 32
    s.jwt_algorithm = "HS256"
    s.jwt_expiration_hours = 1
    s.jwt_audience = ""
    s.jwt_issuer = ""

    with patch("routes.upload.get_settings", return_value=s):
        with patch("auth.jwt.get_settings", return_value=s):
            with TestClient(app) as c:
                resp = c.post(
                    "/submit-upload",
                    files={"file": ("claim.pdf", io.BytesIO(b""), "application/pdf")},
                    headers={"Authorization": f"Bearer {token}"},
                )

    assert resp.status_code == 400


def test_upload_requires_auth(tmp_path):
    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        app.state.redis = None
        yield

    app = FastAPI(lifespan=_lifespan)
    app.include_router(router)

    s = MagicMock()
    s.claim_upload_dir = str(tmp_path)
    s.max_upload_bytes = 10 * 1024 * 1024
    s.jwt_secret_key = "x" * 32
    s.jwt_algorithm = "HS256"
    s.jwt_expiration_hours = 1
    s.jwt_audience = ""
    s.jwt_issuer = ""

    with patch("routes.upload.get_settings", return_value=s):
        with patch("auth.jwt.get_settings", return_value=s):
            with TestClient(app) as c:
                resp = c.post(
                    "/submit-upload",
                    files={"file": ("claim.pdf", io.BytesIO(_VALID_PDF), "application/pdf")},
                )

    assert resp.status_code == 401


def test_each_upload_gets_unique_pipeline_id(tmp_path):
    token = _make_token()

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        app.state.redis = None
        yield

    app = FastAPI(lifespan=_lifespan)
    app.include_router(router)

    s = MagicMock()
    s.claim_upload_dir = str(tmp_path)
    s.max_upload_bytes = 10 * 1024 * 1024
    s.jwt_secret_key = "x" * 32
    s.jwt_algorithm = "HS256"
    s.jwt_expiration_hours = 1
    s.jwt_audience = ""
    s.jwt_issuer = ""

    ids = []
    with patch("routes.upload.get_settings", return_value=s):
        with patch("auth.jwt.get_settings", return_value=s):
            with TestClient(app) as c:
                for _ in range(3):
                    resp = c.post(
                        "/submit-upload",
                        files={"file": ("claim.pdf", io.BytesIO(_VALID_PDF), "application/pdf")},
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    assert resp.status_code == 201
                    ids.append(resp.json()["pipeline_id"])

    assert len(set(ids)) == 3
