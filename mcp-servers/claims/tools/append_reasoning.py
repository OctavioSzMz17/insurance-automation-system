"""Tool: append_reasoning — write-restricted

Append a block of text to an existing claim's `ai_reasoning` column,
preserving the full chain-of-thought across re-analyses and manual
overrides. The caller controls the formatting (tag, timestamp, actor)
so audit blocks stay consistent with the rest of the system.

Used by the orchestrator's POST /claims/{id}/reanalyze endpoint to
fold a fresh recommendation into a claim's history without creating
a duplicate row.

Rules applied:
    Rule 2  — DB write; serialise nothing risky
    Rule 3  — @write_restricted (only auditor+ may invoke via MCP router)
    Rule 4  — Reasoning is append-only; the full audit trail is preserved
    Rule 9  — Structured logging on every call
    Rule 11 — Full type hints
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parents[1]))

from db import get_engine  # noqa: E402
from tools import write_restricted  # noqa: E402

logger = logging.getLogger(__name__)


@write_restricted
async def append_reasoning(claim_id: int, text_to_append: str) -> dict[str, Any]:
    """Concatenate `text_to_append` onto the claim's `ai_reasoning`.

    Inserts a blank line separator between the existing content and the
    new block so successive entries remain visually distinct. Never
    overwrites — this is strictly append-only.

    Args:
        claim_id:       Primary key of the claim to update.
        text_to_append: Free-form text. Caller is responsible for tagging
                        it (e.g. `[RE-ANALYSIS — 2026-05-17T20:00 by Lía] …`)
                        so the frontend parser can render it as its own
                        bubble.

    Returns:
        Updated claim row, same shape as `get_claim_by_id`.

    Raises:
        ValueError:   Claim doesn't exist, or `text_to_append` is empty.
        RuntimeError: DB error.
    """
    if not text_to_append or not text_to_append.strip():
        raise ValueError("text_to_append must be non-empty")

    logger.info(
        f"append_reasoning called | claim_id={claim_id} bytes={len(text_to_append)}"
    )

    try:
        engine = get_engine()
        with engine.begin() as conn:
            # Confirm the claim exists first — surface a clean 404 rather
            # than a silent no-op UPDATE.
            exists = conn.execute(
                text("SELECT 1 FROM claims WHERE claim_id = :id"),
                {"id": claim_id},
            ).fetchone()
            if exists is None:
                raise ValueError(f"Claim {claim_id} not found")

            # COALESCE handles the (rare) NULL ai_reasoning case.
            conn.execute(
                text("""
                    UPDATE claims
                    SET    ai_reasoning = COALESCE(ai_reasoning, '')
                                       || E'\n\n'
                                       || :new_text,
                           updated_at   = NOW()
                    WHERE  claim_id     = :id
                """),
                {"id": claim_id, "new_text": text_to_append},
            )

            row = conn.execute(
                text("""
                    SELECT claim_id, user_id, policy_id, incident_date,
                           incident_description, incident_location,
                           severity_label::text  AS severity_label,
                           reported_damage_amount,
                           claim_status::text    AS claim_status,
                           ai_reasoning,
                           created_at, updated_at
                    FROM   claims
                    WHERE  claim_id = :id
                """),
                {"id": claim_id},
            ).fetchone()

    except ValueError:
        raise
    except Exception as e:
        logger.error(f"append_reasoning failed | claim_id={claim_id} error={e}")
        raise RuntimeError(f"DB update failed for claim_id={claim_id}: {e}") from e

    from datetime import date as date_type
    return {
        "claim_id":               int(row.claim_id),
        "user_id":                int(row.user_id),
        "policy_id":              int(row.policy_id),
        "incident_date":          (
            row.incident_date.isoformat()
            if isinstance(row.incident_date, date_type)
            else str(row.incident_date)
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
        "ai_reasoning":           str(row.ai_reasoning),
        "created_at":             row.created_at.isoformat() if row.created_at else None,
        "updated_at":             row.updated_at.isoformat() if row.updated_at else None,
    }
