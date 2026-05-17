"""Shared fixtures for end-to-end tests (C4.x).

What's provided here:
    e2e_guard          — auto-applied autouse skip when RUN_E2E != "1"
                         OR when LLM_PROVIDER_API_KEY is empty (so missing
                         secrets don't fail CI).
    client             — fastapi.testclient.TestClient with lifespan boot
    db_engine          — SQLAlchemy engine for setup/cleanup queries
    issue_token        — helper that mints a dev JWT for a role
    analyst_token      — convenience: pre-issued analyst JWT
    auditor_token      — convenience: pre-issued auditor JWT
    jp01_pdf_path      — absolute path to the JP01 claim PDF
    reset_approval_caches — wipes the in-memory idempotency/rate caches
                            between tests to avoid pollution
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterator

import pytest

# Make project + orchestrator importable
_ORCH_ROOT   = Path(__file__).resolve().parents[2]   # ai-orchestrator/
_PROJECT_ROOT = _ORCH_ROOT.parent                     # repo root
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_ORCH_ROOT))

# ── Auto-skip when running without secrets / opt-in flag ──────────

def _should_skip() -> tuple[bool, str]:
    if os.environ.get("RUN_E2E") != "1":
        return True, "E2E tests are opt-in. Set RUN_E2E=1 to run them."
    if not os.environ.get("LLM_PROVIDER_API_KEY"):
        return True, "LLM_PROVIDER_API_KEY is empty — refusing to make real LLM calls."
    if not os.environ.get("DATABASE_URL"):
        return True, "DATABASE_URL is empty — refusing to hit a missing DB."
    return False, ""


@pytest.fixture(autouse=True)
def e2e_guard(request: pytest.FixtureRequest) -> None:
    """Skip every test in this directory unless real services are wired."""
    skip, reason = _should_skip()
    if skip:
        pytest.skip(reason)


# ── TestClient with lifespan ──────────────────────────────────────

@pytest.fixture(scope="session")
def client():
    """Boot the FastAPI app inside a TestClient context.

    The `with` block invokes the lifespan (which builds the MCPRouter
    + AgentRouter once on app.state). All tests in the session share
    one client to keep that boot cost down to a single hit.
    """
    from fastapi.testclient import TestClient
    from app import app  # type: ignore[import-not-found]  # noqa: WPS433

    with TestClient(app) as c:
        yield c


# ── DB engine for setup/cleanup ───────────────────────────────────

@pytest.fixture(scope="session")
def db_engine():
    from sqlalchemy import create_engine
    return create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)


# ── JWT helpers ───────────────────────────────────────────────────

@pytest.fixture(scope="session")
def issue_token():
    from auth import Role, create_access_token  # type: ignore[import-not-found]  # noqa: WPS433

    def _issue(sub: str, role: str) -> str:
        return create_access_token(sub=sub, role=Role(role))

    return _issue


@pytest.fixture(scope="session")
def analyst_token(issue_token) -> str:
    return issue_token("alice.e2e", "analyst")


@pytest.fixture(scope="session")
def auditor_token(issue_token) -> str:
    return issue_token("octav.e2e", "auditor")


# ── JP01 sample PDF path ──────────────────────────────────────────

@pytest.fixture(scope="session")
def jp01_pdf_path() -> str:
    return r"C:\Users\octav\OneDrive\Escritorio\Real-World Project\Claim_Report_JP01.pdf"


# ── reset approval caches between tests ───────────────────────────

@pytest.fixture(autouse=True)
def reset_approval_caches() -> Iterator[None]:
    """Wipe idempotency + rate-limit caches so tests don't poison each other."""
    from validation import reset_caches  # type: ignore[import-not-found]  # noqa: WPS433

    reset_caches()
    yield
    reset_caches()
