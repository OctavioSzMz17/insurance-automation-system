"""Security-critical integration tests for the documents MCP.

Coverage:
- Happy path: real PDF in CLAIM_UPLOAD_DIR -> sha256 + text returned.
- Path traversal via pipeline_id ("../etc/passwd", absolute path, '..\\foo') -> Pydantic UUID rejects.
- Symlink escape: pipeline_id matches a symlink whose target is outside UPLOAD_DIR -> rejected.
- Missing file -> kind:business 'pdf not found' (does NOT echo path).
- Malformed PDF bytes -> kind:business 'pdf is malformed'.
- CLAIM_UPLOAD_DIR=/tmp in production env -> startup validation fails.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

from _common.settings import MCPSettings

pytestmark = pytest.mark.asyncio


def _build_tools_map(settings: MCPSettings):
    from documents.tools import build_tools

    return {s.name: s for s in build_tools(settings)}


def _wrap(handler):
    from _common.errors import classified

    return classified(handler)


def _settings() -> MCPSettings:
    return MCPSettings(mcp_domain="documents")  # type: ignore[call-arg]


# ── happy path ──────────────────────────────────────────────────────────────


async def test_compute_sha256_of_valid_pdf(pipeline_with_pdf):
    tools = _build_tools_map(_settings())
    res = await _wrap(tools["compute_sha256"].handler)(
        {
            "pipeline_id": str(pipeline_with_pdf),
            "caller_sub": "analyst@example.com",
            "caller_role": "ANALYST",
        }
    )
    assert "error" not in res, res
    assert len(res["sha256"]) == 64
    int(res["sha256"], 16)  # is hex


async def test_extract_text_of_valid_pdf(pipeline_with_pdf):
    tools = _build_tools_map(_settings())
    res = await _wrap(tools["extract_text"].handler)(
        {
            "pipeline_id": str(pipeline_with_pdf),
            "caller_sub": "analyst@example.com",
            "caller_role": "ANALYST",
        }
    )
    assert "error" not in res, res
    assert res["page_count"] == 1
    assert "text" in res
    assert res["truncated"] is False


async def test_parse_pdf_combines(pipeline_with_pdf):
    tools = _build_tools_map(_settings())
    res = await _wrap(tools["parse_pdf"].handler)(
        {
            "pipeline_id": str(pipeline_with_pdf),
            "caller_sub": "analyst@example.com",
            "caller_role": "ANALYST",
        }
    )
    assert "error" not in res, res
    assert "sha256" in res and "text" in res and "page_count" in res


# ── path traversal via pipeline_id ──────────────────────────────────────────


@pytest.mark.parametrize(
    "evil",
    [
        "../etc/passwd",
        "..\\..\\secrets",
        "/etc/passwd",
        "C:\\Windows\\System32",
        "00000000-0000-0000-0000-000000000000/../escape",
        "00000000-0000-0000-0000-00000000000g",  # invalid hex
    ],
)
async def test_path_traversal_via_pipeline_id_rejected(upload_dir, evil):
    tools = _build_tools_map(_settings())
    res = await _wrap(tools["compute_sha256"].handler)(
        {
            "pipeline_id": evil,
            "caller_sub": "analyst@example.com",
            "caller_role": "ANALYST",
        }
    )
    # Pydantic UUID validator MUST reject everything that is not a strict UUID.
    assert res["kind"] == "business"
    # The server's actual upload_dir must never appear in the response. Echoing the
    # caller's own malformed input back (e.g. 'C:\\Windows\\System32') is not a leak —
    # it discloses nothing the caller did not already know.
    assert str(upload_dir) not in str(res)


# ── symlink escape ─────────────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs admin on Windows")
async def test_symlink_escape_rejected(upload_dir, tmp_path):
    # Create a real file OUTSIDE upload_dir and a symlink INSIDE upload_dir pointing to it.
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4 evil")
    pid = uuid.uuid4()
    inside_link = upload_dir / f"{pid}.pdf"
    os.symlink(outside, inside_link)
    # safe_pdf_path resolves the symlink and verifies the *resolved* target lives under upload_dir.
    tools = _build_tools_map(_settings())
    res = await _wrap(tools["compute_sha256"].handler)(
        {
            "pipeline_id": str(pid),
            "caller_sub": "analyst@example.com",
            "caller_role": "ANALYST",
        }
    )
    assert res["kind"] == "business"
    assert "outside" not in res.get("error", "")


# ── missing file ───────────────────────────────────────────────────────────


async def test_missing_file_returns_business_not_internal(upload_dir):
    tools = _build_tools_map(_settings())
    res = await _wrap(tools["compute_sha256"].handler)(
        {
            "pipeline_id": str(uuid.uuid4()),
            "caller_sub": "analyst@example.com",
            "caller_role": "ANALYST",
        }
    )
    assert res["kind"] == "business"
    assert res["error"] == "pdf not found"
    # critically: do not echo the resolved path
    assert str(upload_dir) not in str(res)


# ── malformed PDF ──────────────────────────────────────────────────────────


async def test_malformed_pdf_is_business_error(upload_dir):
    pid = uuid.uuid4()
    (upload_dir / f"{pid}.pdf").write_bytes(b"not a pdf at all")
    tools = _build_tools_map(_settings())
    res = await _wrap(tools["extract_text"].handler)(
        {
            "pipeline_id": str(pid),
            "caller_sub": "analyst@example.com",
            "caller_role": "ANALYST",
        }
    )
    assert res["kind"] == "business"
    assert "malformed" in res["error"]


# ── extra fields blocked ───────────────────────────────────────────────────


async def test_extra_field_rejected(pipeline_with_pdf):
    tools = _build_tools_map(_settings())
    res = await _wrap(tools["compute_sha256"].handler)(
        {
            "pipeline_id": str(pipeline_with_pdf),
            "caller_sub": "analyst@example.com",
            "caller_role": "ANALYST",
            "filename": "../../../etc/passwd",  # extra=forbid kills this
        }
    )
    assert res["kind"] == "business"


# ── startup validation: /tmp in production env ─────────────────────────────


async def test_startup_validation_rejects_tmp_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CLAIM_UPLOAD_DIR", "/tmp/some/sub")
    with pytest.raises(Exception):
        _settings().validate_upload_dir()
