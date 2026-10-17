"""Fixtures for the policies MCP. Shares the seed + engine-reset pattern from claims."""
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

from scripts.seed.fixtures import (  # noqa: E402
    ADMIN_SUB,
    ANALYST_ID,
    ANALYST_SUB,
    JUAN_ID,
    JUAN_SUB,
    POLICY_AUTO_ID,
    POLICY_HOME_ID,
)
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
def other_user_with_policy(db_conn):
    """Insert a second CLIENT plus a policy they own, to test cross-tenant denial.

    Migration 0004 renamed the owning table to `clients` and `policies.user_id` to
    `policies.client_id`. The repository still aliases the column back to `user_id`
    in its SELECT output, so only the inserts change here — the tool contract did not.

    The yielded keys keep their original names for the same reason.
    """
    from datetime import date

    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO clients (email, full_name)
            VALUES ('victim@example.com', 'Vic Tim')
            ON CONFLICT (email) DO UPDATE SET full_name = EXCLUDED.full_name
            RETURNING id::text, email
            """,
        )
        user_id, user_email = cur.fetchone()
        cur.execute(
            """
            INSERT INTO policies
                (client_id, status, deductible_usd, coverage_limit_usd, effective_from, effective_to, coverage_rules)
            VALUES (%s, 'ACTIVE', 250.00, 10000.00, %s, %s, '[]'::jsonb)
            RETURNING id::text
            """,
            (user_id, date(2026, 1, 1), date(2026, 12, 31)),
        )
        policy_id = cur.fetchone()[0]
    db_conn.commit()
    yield {"user_id": user_id, "user_email": user_email, "policy_id": policy_id}
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM policies WHERE id = %s", (policy_id,))
        cur.execute("DELETE FROM clients WHERE id = %s", (user_id,))
    db_conn.commit()


@pytest.fixture(scope="session")
def fixture_ids():
    return {
        "analyst_sub": ANALYST_SUB,
        "admin_sub": ADMIN_SUB,
        "analyst_id": ANALYST_ID,
        "policy_auto_id": POLICY_AUTO_ID,
        "policy_home_id": POLICY_HOME_ID,
        # The seeded policies belong to a client, so the owner-reads-own-policy path
        # needs that client's identity, not a staff one.
        "client_sub": JUAN_SUB,
        "client_id": JUAN_ID,
    }
