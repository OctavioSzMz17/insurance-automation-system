"""Tool: list_claims — bulk read for adjuster dashboard.

Returns a paginated list of claim rows with optional status filter.
Used by the orchestrator's GET /claims endpoint to populate the
adjuster's review queue.

NOT @write_restricted — pure read, callable by anyone.

Rules applied:
    Rule 2  — Read-only DB access; monetary fields serialised as str
    Rule 3  — Stays in the claims domain
    Rule 9  — Structured logging; no print()
    Rule 11 — Full type hints
"""

from __future__ import annotations

import logging
import sys
from datetime import date as date_type
from pathlib import Path
from typing import Any

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parents[1]))

from db import get_engine  # noqa: E402

logger = logging.getLogger(__name__)

_ALLOWED_STATUSES = {
    "PENDING", "MANUAL_REVIEW", "APPROVED",
    "REJECTED", "AWAITING_INPUT",
}


async def list_claims(
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Fetch a list of claim rows ordered by most-recent-first.

    Args:
        status: Optional `claim_status` filter (e.g. "MANUAL_REVIEW"). If
            omitted, all statuses are returned.
        limit:  Max rows to return (1-500, default 100).
        offset: Skip this many rows before returning (default 0).

    Returns:
        A list of dicts shaped like `get_claim_by_id` output. Empty list
        when no rows match the filter.

    Raises:
        ValueError: If `status` is not a recognised claim_status value or
            limit/offset are out of range.
        RuntimeError: If the database query fails.
    """
    # ── Input validation ─────────────────────────────────────
    if status is not None and status not in _ALLOWED_STATUSES:
        raise ValueError(
            f"Invalid status '{status}'. "
            f"Allowed: {sorted(_ALLOWED_STATUSES)}"
        )
    if not (1 <= limit <= 500):
        raise ValueError(f"limit must be 1-500, got {limit}")
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset}")

    logger.info(
        f"list_claims called | status={status} limit={limit} offset={offset}"
    )

    # ── Build query with optional WHERE ──────────────────────
    sql = """
        SELECT claim_id, user_id, policy_id, incident_date,
               incident_description, incident_location,
               severity_label::text AS severity_label,
               reported_damage_amount,
               claim_status::text   AS claim_status,
               ai_reasoning,
               created_at, updated_at
        FROM   claims
    """
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if status is not None:
        sql += " WHERE claim_status = :status"
        params["status"] = status
    sql += " ORDER BY created_at DESC NULLS LAST, claim_id DESC"
    sql += " LIMIT :limit OFFSET :offset"

    try:
        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(text(sql), params)
            rows = result.fetchall()
    except Exception as e:
        logger.error(f"list_claims failed | error={e}")
        raise RuntimeError(f"Database query failed: {e}") from e

    out: list[dict[str, Any]] = []
    for row in rows:
        out.append({
            "claim_id":               int(row.claim_id),
            "user_id":                int(row.user_id),
            "policy_id":              int(row.policy_id),
            "incident_date":          (
                row.incident_date.isoformat()
                if isinstance(row.incident_date, date_type)
                else str(row.incident_date) if row.incident_date else None
            ),
            "incident_description":   str(row.incident_description),
            "incident_location":      (
                str(row.incident_location) if row.incident_location else None
            ),
            "severity_label":         str(row.severity_label),
            "reported_damage_amount": (
                str(row.reported_damage_amount)
                if row.reported_damage_amount is not None else None
            ),
            "claim_status":           str(row.claim_status),
            "ai_reasoning":           str(row.ai_reasoning) if row.ai_reasoning else "",
            "created_at":             row.created_at.isoformat() if row.created_at else None,
            "updated_at":             row.updated_at.isoformat() if row.updated_at else None,
        })

    logger.info(f"list_claims returned {len(out)} rows")
    return out
