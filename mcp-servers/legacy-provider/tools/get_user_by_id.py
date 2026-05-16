"""Tool: get_user_by_id — A3.3

Fetches a single user record from the PostgreSQL users table by
their integer primary key.

Input schema (enforced by MCP server):
    user_id  (int, required) — positive integer primary key

Output:
    dict with keys: user_id (int), name (str), email (str)

Raises:
    ValueError   — user not found; agent should treat claim as REJECTED
    RuntimeError — DB connection or query failure

Rules applied:
    Rule 2  — Read-only DB access through this MCP tool only
    Rule 9  — Structured logging; no print()
    Rule 11 — Full type hints
"""

import logging
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

# Allow running directly (Claude Desktop / local test) without relative imports
sys.path.insert(0, str(Path(__file__).parents[1]))
from db import get_engine

logger = logging.getLogger(__name__)


async def get_user_by_id(user_id: int) -> dict[str, Any]:
    """Fetch one user by primary key.

    Args:
        user_id: Integer primary key of the user to retrieve.

    Returns:
        Dict containing user_id (int), name (str), email (str).

    Raises:
        ValueError: If no user with the given ID exists.
        RuntimeError: If the database query fails.
    """
    logger.info(f"get_user_by_id called | user_id={user_id}")

    try:
        engine = get_engine()

        with engine.connect() as conn:
            result = conn.execute(
                text("""
                    SELECT user_id, name, email
                    FROM   users
                    WHERE  user_id = :user_id
                """),
                {"user_id": user_id},
            )
            row = result.fetchone()

        if row is None:
            logger.warning(f"get_user_by_id | user_id={user_id} not found")
            raise ValueError(f"User {user_id} not found")

        user = {
            "user_id": int(row.user_id),
            "name":    str(row.name),
            "email":   str(row.email),
        }
        logger.info(f"get_user_by_id result | user_id={user_id} email={user['email']}")
        return user

    except ValueError:
        raise  # Let ValueError propagate as-is — agent handles it
    except Exception as e:
        logger.error(f"get_user_by_id failed | user_id={user_id} error={e}")
        raise RuntimeError(f"Database query failed for user_id={user_id}: {e}") from e
