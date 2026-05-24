"""Tool: get_policies_by_user_id — A3.2

Fetches all insurance policies belonging to a given user from the
PostgreSQL policies table.

Input schema (enforced by MCP server):
    user_id  (int, required) — positive integer primary key of the user

Output:
    List of dicts, each containing:
        policy_id          (int)
        user_id            (int)
        type               (str)   e.g. "auto", "home", "health"
        status             (str)   one of PolicyStatus enum values
        deductible_usd     (str)   Decimal serialised as string (Rule 2)
        coverage_limit_usd (str)   Decimal serialised as string (Rule 2)
        start_date         (str)   ISO-8601 date
        end_date           (str)   ISO-8601 date

    Returns an empty list if the user has no policies.

Raises:
    RuntimeError — DB connection or query failure

Rules applied:
    Rule 2  — Read-only DB access; monetary fields serialised as str, never float
    Rule 9  — Structured logging; no print()
    Rule 11 — Full type hints
"""

import asyncio
import logging
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text

# Allow running directly (Claude Desktop / local test) without relative imports
sys.path.insert(0, str(Path(__file__).parents[1]))
from db import get_engine

logger = logging.getLogger(__name__)


def _serialize_row(row: Any) -> dict[str, Any]:
    """Convert a SQLAlchemy Row into a JSON-safe dict.

    Monetary fields (Decimal) become strings to preserve precision.
    Date fields become ISO-8601 strings.

    Args:
        row: A SQLAlchemy Row object from a policies query.

    Returns:
        Plain dict safe for JSON serialisation.
    """
    return {
        "policy_id":          int(row.policy_id),
        "user_id":            int(row.user_id),
        "type":               str(row.type),
        "status":             str(row.status),
        "deductible_usd":     str(row.deductible_usd),
        "coverage_limit_usd": str(row.coverage_limit_usd),
        "start_date":         row.start_date.isoformat() if isinstance(row.start_date, date) else str(row.start_date),
        "end_date":           row.end_date.isoformat()   if isinstance(row.end_date,   date) else str(row.end_date),
    }


async def get_policies_by_user_id(user_id: int) -> list[dict[str, Any]]:
    """Fetch all policies belonging to a user.

    Args:
        user_id: Integer primary key of the user.

    Returns:
        List of policy dicts (may be empty if the user has no policies).
        Monetary fields are strings to preserve Decimal precision (Rule 2).

    Raises:
        RuntimeError: If the database query fails.
    """
    logger.info(f"get_policies_by_user_id called | user_id={user_id}")

    # Fix A — run sync SQLAlchemy in a thread pool to avoid blocking the event loop.
    def _fetch() -> list:
        engine = get_engine()
        with engine.connect() as conn:
            return conn.execute(
                text("""
                    SELECT policy_id, user_id, type, status,
                           deductible_usd, coverage_limit_usd,
                           start_date, end_date
                    FROM   policies
                    WHERE  user_id = :user_id
                    ORDER  BY policy_id
                """),
                {"user_id": user_id},
            ).fetchall()

    try:
        rows = await asyncio.to_thread(_fetch)
    except Exception as e:
        logger.error(f"get_policies_by_user_id failed | user_id={user_id} error={e}")
        raise RuntimeError(f"Database query failed for user_id={user_id}: {e}") from e

    policies = [_serialize_row(row) for row in rows]
    logger.info(f"get_policies_by_user_id result | user_id={user_id} count={len(policies)}")
    return policies
