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

_ALLOWED_STATUSES = {
    "PENDING", "MANUAL_REVIEW", "APPROVED",
    "REJECTED", "AWAITING_INPUT",
}


async def list_claims(
    status:     str | None = None,
    limit:      int = 100,
    offset:     int = 0,
    user_email: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch a list of claim rows ordered by most-recent-first.

    Fix #70 — `user_email` enables tenant isolation for ANALYST callers.
    When set, only claims whose owner's email matches `user_email` are
    returned (JOIN against the users table, enforced at DB level).

    Args:
        status:     Optional `claim_status` filter (e.g. "MANUAL_REVIEW").
        limit:      Max rows to return (1-500, default 100).
        offset:     Skip this many rows before returning (default 0).
        user_email: When set, restrict results to claims owned by this
                    email address. Passed by orchestrator for ANALYST role.

    Returns:
        A list of dicts shaped like `get_claim_by_id` output.

    Raises:
        ValueError:   If params are out of range or status is unknown.
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
        "list_claims called | status=%s limit=%d offset=%d user_email=%s",
        status, limit, offset,
        "<filtered>" if user_email else "all",
    )

    # ── Build query ───────────────────────────────────────────
    # When user_email is provided, JOIN users so the filter is applied
    # at the DB level — no post-fetch slicing (Fix #70).
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    conditions: list[str] = []

    if user_email is not None:
        from_clause = """
            FROM   claims c
            JOIN   users  u ON u.user_id = c.user_id
        """
        select_prefix = "c."
        conditions.append("LOWER(u.email) = LOWER(:user_email)")
        params["user_email"] = user_email
    else:
        from_clause    = "FROM claims c"
        select_prefix  = "c."

    if status is not None:
        conditions.append(f"{select_prefix}claim_status = :status")
        params["status"] = status

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    sql = f"""
        SELECT {select_prefix}claim_id,   {select_prefix}user_id,
               {select_prefix}policy_id,  {select_prefix}incident_date,
               {select_prefix}incident_description, {select_prefix}incident_location,
               {select_prefix}severity_label::text        AS severity_label,
               {select_prefix}reported_damage_amount,
               {select_prefix}claim_status::text          AS claim_status,
               {select_prefix}ai_reasoning,
               {select_prefix}created_at, {select_prefix}updated_at
        {from_clause}
        {where_clause}
        ORDER BY {select_prefix}created_at DESC NULLS LAST,
                 {select_prefix}claim_id    DESC
        LIMIT :limit OFFSET :offset
    """

    # Fix A — run sync SQLAlchemy in a thread pool to avoid blocking the
    # event loop. sql and params are captured by the closure; both are
    # immutable at this point so thread safety is not a concern.
    def _fetch() -> list:
        engine = get_engine()
        with engine.connect() as conn:
            return conn.execute(text(sql), params).fetchall()

    try:
        rows = await asyncio.to_thread(_fetch)
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
