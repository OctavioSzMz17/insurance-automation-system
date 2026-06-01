"""Fixtures for the users MCP."""
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

from scripts.seed.fixtures import ADMIN_ID, ADMIN_SUB, ANALYST_ID, ANALYST_SUB  # noqa: E402
from scripts.seed.seed_dev import seed as run_seed  # noqa: E402

psycopg2.extras.register_uuid()


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    return dsn


@pytest.fixture(scope="session", autouse=True)
def _ensure_seed():
    run_seed()


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_between_tests():
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
def other_user(db_conn):
    """Insert a second user with a known id+email; used to test IDOR denial."""
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (email, role, full_name)
            VALUES ('stranger@example.com', 'ANALYST', 'Stra Nger')
            ON CONFLICT (email) DO UPDATE SET role = EXCLUDED.role
            RETURNING id::text, email
            """,
        )
        user_id, email = cur.fetchone()
    db_conn.commit()
    yield {"id": user_id, "email": email}
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    db_conn.commit()


@pytest.fixture(scope="session")
def fixture_ids():
    return {
        "analyst_sub": ANALYST_SUB,
        "analyst_id": ANALYST_ID,
        "admin_sub": ADMIN_SUB,
        "admin_id": ADMIN_ID,
    }
