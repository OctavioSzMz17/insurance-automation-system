"""Fixtures for the documents MCP. Each test gets an isolated upload dir under tmp_path."""
from __future__ import annotations

import io
import os
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MCP_ROOT = _REPO_ROOT / "mcp-servers"
for p in (_REPO_ROOT, _MCP_ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

load_dotenv(_REPO_ROOT / ".env")


@pytest.fixture
def upload_dir(tmp_path, monkeypatch) -> Path:
    """Per-test isolated upload directory; point CLAIM_UPLOAD_DIR at it."""
    d = tmp_path / "uploads"
    d.mkdir()
    monkeypatch.setenv("CLAIM_UPLOAD_DIR", str(d))
    monkeypatch.setenv("ENVIRONMENT", "development")
    return d


@pytest.fixture
def sample_pdf_bytes() -> bytes:
    """Minimal valid PDF (one blank page) — built with pypdf so the test does not depend on
    a binary fixture file in the repo."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture
def pipeline_with_pdf(upload_dir, sample_pdf_bytes):
    pid = uuid.uuid4()
    (upload_dir / f"{pid}.pdf").write_bytes(sample_pdf_bytes)
    return pid
