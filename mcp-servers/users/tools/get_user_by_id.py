"""Tool: get_user_by_id — Users MCP.

Fetches a single user record from the PostgreSQL users table by
integer primary key.
"""

import logging
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parents[1]))
from db import get_engine

logger = logging.getLogger(__name__)


async def get_user_by_id(user_id: int) -> dict[str, Any]:
    """Fetch one user by primary key."""
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
            "name": str(row.name),
            "email": str(row.email),
        }
        # Fix #95 — email is PII; omit from INFO logs so it doesn't
        # flow to external log aggregators (Langfuse, CloudWatch, etc.).
        logger.debug("get_user_by_id result | user_id=%s found=True", user_id)
        return user
    except ValueError:
        raise
    except Exception as e:
        logger.error(f"get_user_by_id failed | user_id={user_id} error={e}")
        raise RuntimeError(f"Database query failed for user_id={user_id}: {e}") from e

