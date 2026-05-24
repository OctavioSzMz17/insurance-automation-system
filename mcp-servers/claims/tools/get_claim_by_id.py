"""Tool: get_claim_by_id — C1.2

Read-only lookup for a single claim by its integer claim_id. Used by
the orchestrator's GET /claims/{claim_id} endpoint.

NOT @write_restricted — it's a pure read, callable by anyone.

Rules applied:
    Rule 2  — Read-only DB access; monetary fields serialised as str
    Rule 3  — Stays in the claims domain
    Rule 9  — Structured logging; no print()
    Rule 11 — Full type hints
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date as date_type
from pathlib import Path
from typing import Any

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parents[1]))

from db import get_engine  # noqa: E402

logger = logging.getLogger(__name__)


def _fetch_claim_row(claim_id: int) -> Any:
    """Sync DB fetch — runs in a thread pool via asyncio.to_thread (Fix A)."""
    engine = get_engine()
    with engine.connect() as conn:
        return conn.execute(
            text("""
                SELECT claim_id, user_id, policy_id, incident_date,
                       incident_description, incident_location,
                       severity_label::text  AS severity_label,
                       reported_damage_amount,
                       claim_status::text    AS claim_status,
                       ai_reasoning,
                       created_at, updated_at
                FROM   claims
                WHERE  claim_id = :claim_id
            """),
            {"claim_id": claim_id},
        ).fetchone()


async def get_claim_by_id(claim_id: int) -> dict[str, Any]:
    """Fetch one claim by primary key.

    Args:
        claim_id: BIGSERIAL primary key from the claims table.

    Returns:
        Dict with every column from the claims row, monetary fields as
        strings and dates as ISO-8601 strings.

    Raises:
        ValueError: If no claim with the given ID exists.
        RuntimeError: If the database query fails.
    """
    logger.info(f"get_claim_by_id called | claim_id={claim_id}")

    # Fix A — run the sync SQLAlchemy call in a thread pool so the asyncio
    # event loop is not blocked while waiting for the DB round-trip.
    try:
        row = await asyncio.to_thread(_fetch_claim_row, claim_id)
    except Exception as e:
        logger.error(f"get_claim_by_id failed | claim_id={claim_id} error={e}")
        raise RuntimeError(f"Database query failed for claim_id={claim_id}: {e}") from e

    if row is None:
        logger.warning(f"get_claim_by_id | claim_id={claim_id} not found")
        raise ValueError(f"Claim {claim_id} not found")

    return {
        "claim_id":               int(row.claim_id),
        "user_id":                int(row.user_id),
        "policy_id":              int(row.policy_id),
        "incident_date":          row.incident_date.isoformat() if isinstance(row.incident_date, date_type) else str(row.incident_date),
        "incident_description":   str(row.incident_description),
        "incident_location":      str(row.incident_location) if row.incident_location else None,
        "severity_label":         str(row.severity_label),
        "reported_damage_amount": str(row.reported_damage_amount) if row.reported_damage_amount is not None else None,
        "claim_status":           str(row.claim_status),
        "ai_reasoning":           str(row.ai_reasoning),
        "created_at":             row.created_at.isoformat() if row.created_at else None,
        "updated_at":             row.updated_at.isoformat() if row.updated_at else None,
    }
