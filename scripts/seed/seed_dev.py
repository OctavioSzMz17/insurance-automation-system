"""Idempotent dev seeder. Run with: python -m scripts.seed.seed_dev

Uses INSERT ... ON CONFLICT DO UPDATE so re-running converges to the fixture state.
Does NOT touch claims/documents — those are created by tests on demand.
"""
from __future__ import annotations

import json
import os
import sys

import psycopg2
import psycopg2.extras
from psycopg2.extras import Json

psycopg2.extras.register_uuid()

# Allow `python scripts/seed/seed_dev.py` as well as `python -m scripts.seed.seed_dev`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.seed.fixtures import CLIENTS, POLICIES, STAFF_IDS, STAFF_USERS  # noqa: E402


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required to run the seeder")
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    return dsn


def seed() -> None:
    conn = psycopg2.connect(_dsn())
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            # ── insured clients ──────────────────────────────────────────────
            for c in CLIENTS:
                cur.execute(
                    """
                    INSERT INTO clients (id, email, full_name)
                    VALUES (%(id)s, %(email)s, %(full_name)s)
                    ON CONFLICT (id) DO UPDATE
                    SET email = EXCLUDED.email,
                        full_name = EXCLUDED.full_name
                    """,
                    c,
                )
            # ── system staff ─────────────────────────────────────────────────
            for u in STAFF_USERS:
                cur.execute(
                    """
                    INSERT INTO users (id, email, role, full_name, password_hash)
                    VALUES (%(id)s, %(email)s, %(role)s, %(full_name)s, %(password_hash)s)
                    ON CONFLICT (id) DO UPDATE
                    SET email = EXCLUDED.email,
                        role = EXCLUDED.role,
                        full_name = EXCLUDED.full_name,
                        password_hash = EXCLUDED.password_hash
                    """,
                    u,
                )
            for p in POLICIES:
                payload = dict(p)
                payload["coverage_rules"] = Json(payload["coverage_rules"])
                cur.execute(
                    """
                    INSERT INTO policies (
                        id, client_id, policy_type, status, deductible_usd, coverage_limit_usd,
                        effective_from, effective_to, coverage_rules
                    )
                    VALUES (
                        %(id)s, %(client_id)s, %(policy_type)s, %(status)s,
                        %(deductible_usd)s, %(coverage_limit_usd)s,
                        %(effective_from)s, %(effective_to)s, %(coverage_rules)s
                    )
                    ON CONFLICT (id) DO UPDATE
                    SET client_id = EXCLUDED.client_id,
                        policy_type = EXCLUDED.policy_type,
                        status = EXCLUDED.status,
                        deductible_usd = EXCLUDED.deductible_usd,
                        coverage_limit_usd = EXCLUDED.coverage_limit_usd,
                        effective_from = EXCLUDED.effective_from,
                        effective_to = EXCLUDED.effective_to,
                        coverage_rules = EXCLUDED.coverage_rules
                    """,
                    payload,
                )
            # ── cleanup: the 0004 rename moved the old `users` rows (system
            #    staff) into `clients`. Now that policies point at real clients,
            #    purge any staff identity that leaked into the clients table.
            cur.execute(
                "DELETE FROM clients WHERE id = ANY(%(ids)s::uuid[])",
                {"ids": [str(i) for i in STAFF_IDS]},
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    seed()
    summary = {
        "seeded_clients": len(CLIENTS),
        "seeded_staff_users": len(STAFF_USERS),
        "seeded_policies": len(POLICIES),
    }
    sys.stdout.write(json.dumps({"event": "seed.ok", **summary}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
