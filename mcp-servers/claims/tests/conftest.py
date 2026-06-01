"""Test fixtures for the claims MCP. Talks to the real Postgres (Neon) via DATABASE_URL.

Path setup: each MCP's tests live in `mcp-servers/<domain>/tests/`. We add the monorepo
root and `mcp-servers/` to sys.path so `from _common.X` and `from packages.common.X`
resolve identically to the runtime.
"""
from __future__ import annotations

import os
import sys
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

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

from scripts.seed.fixtures import ADMIN_SUB, ANALYST_ID, ANALYST_SUB, POLICY_AUTO_ID  # noqa: E402
from scripts.seed.seed_dev import seed as run_seed  # noqa: E402

psycopg2.extras.register_uuid()


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set; skipping integration tests")
    return dsn


@pytest.fixture(scope="session", autouse=True)
def _ensure_seed():
    run_seed()


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_between_tests():
    """The async engine is process-singleton (Rule 2), but pytest-asyncio gives each test
    a fresh event loop. We dispose the engine after every test so the next test rebinds it.
    """
    yield
    from _common import db as _db

    await _db.dispose_engine()


@pytest.fixture
def db_conn():
    conn = psycopg2.connect(_dsn())
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def claim_owned_by_analyst(db_conn):
    """Insert a claim owned by the analyst fixture user, yield its id, then delete."""
    claim_id = _insert_claim(
        db_conn,
        submitted_by_sub=ANALYST_SUB,
        pdf_sha256="a" * 64,
    )
    yield claim_id
    _delete_claim(db_conn, claim_id)


@pytest.fixture
def claim_owned_by_other(db_conn):
    """A claim NOT owned by the analyst; used to test cross-tenant denial."""
    other_sub = "someone-else@example.com"
    claim_id = _insert_claim(
        db_conn,
        submitted_by_sub=other_sub,
        pdf_sha256="b" * 64,
    )
    yield claim_id
    _delete_claim(db_conn, claim_id)


def _insert_claim(conn, *, submitted_by_sub: str, pdf_sha256: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO claims
                (submitted_by_sub, policy_id, pdf_sha256, reported_damage_amount, severity, status)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (submitted_by_sub, pdf_sha256) DO UPDATE SET status = EXCLUDED.status
            RETURNING id::text
            """,
            (submitted_by_sub, POLICY_AUTO_ID, pdf_sha256, "1500.00", "MEDIUM", "SUBMITTED"),
        )
        claim_id = cur.fetchone()[0]
    conn.commit()
    return claim_id


def _delete_claim(conn, claim_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM claims WHERE id = %s", (claim_id,))
    conn.commit()


@pytest.fixture(scope="session")
def fixture_subs():
    return {"analyst": ANALYST_SUB, "admin": ADMIN_SUB, "analyst_id": ANALYST_ID}
