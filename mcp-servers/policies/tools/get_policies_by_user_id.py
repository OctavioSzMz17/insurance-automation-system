"""Tool: get_policies_by_user_id — Policies MCP.

Fetches all insurance policies for a user in a read-only manner.
"""

import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parents[1]))
from db import get_engine

logger = logging.getLogger(__name__)


def _serialize_row(row: Any) -> dict[str, Any]:
    return {
        "policy_id": int(row.policy_id),
        "user_id": int(row.user_id),
        "type": str(row.type),
        "status": str(row.status),
        "deductible_usd": str(row.deductible_usd),
        "coverage_limit_usd": str(row.coverage_limit_usd),
        "start_date": row.start_date.isoformat() if isinstance(row.start_date, date) else str(row.start_date),
        "end_date": row.end_date.isoformat() if isinstance(row.end_date, date) else str(row.end_date),
    }


async def get_policies_by_user_id(user_id: int) -> list[dict[str, Any]]:
    logger.info(f"get_policies_by_user_id called | user_id={user_id}")
    try:
        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(
                text("""
                    SELECT policy_id, user_id, type, status,
                           deductible_usd, coverage_limit_usd,
                           start_date, end_date
                    FROM   policies
                    WHERE  user_id = :user_id
                    ORDER  BY policy_id
                """),
                {"user_id": user_id},
            )
            rows = result.fetchall()
        policies = [_serialize_row(row) for row in rows]
        logger.info(
            f"get_policies_by_user_id result | user_id={user_id} "
            f"count={len(policies)}"
        )
        return policies
    except Exception as e:
        logger.error(f"get_policies_by_user_id failed | user_id={user_id} error={e}")
        raise RuntimeError(f"Database query failed for user_id={user_id}: {e}") from e

