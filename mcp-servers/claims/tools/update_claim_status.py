"""Tool: update_claim_status — C1.3

Manual override of a claim's status. Today (C1.3) this is invoked only
from POST /claims/{claim_id}/approve to move a MANUAL_REVIEW / AWAITING_INPUT
claim to APPROVED (or REJECTED). It is WRITE-RESTRICTED — once JWT lands
in C1.4, only the `auditor` role may reach it.

State machine enforced here (DB-level safety net):
    AWAITING_INPUT  → APPROVED | REJECTED
    MANUAL_REVIEW   → APPROVED | REJECTED
    APPROVED        → (frozen, 409)
    REJECTED        → (frozen, 409)

Appends an audit line to ai_reasoning so the original chain-of-thought
is preserved (Rule 4).

Rules applied:
    Rule 2  — Monetary fields untouched; just a status + reasoning update
    Rule 3  — @write_restricted; centralised RBAC will live in C1.5
    Rule 4  — ai_reasoning is APPENDED, never overwritten
    Rule 9  — Structured logging on every state transition
    Rule 11 — Type hints throughout
"""

from __future__ import annotations

import importlib.util as _ilu
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

# Allow direct execution and loader-based import
sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parents[3]))

from db import get_engine  # noqa: E402

# Pull the write_restricted decorator without breaking when this module is
# loaded standalone (same pattern as create_claim.py).
_marker_path = Path(__file__).parent / "__init__.py"
_spec = _ilu.spec_from_file_location("_claims_tools_init_uc", _marker_path)
_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)        # type: ignore[union-attr]
write_restricted = _mod.write_restricted

logger = logging.getLogger(__name__)

# Statuses we accept as the NEW status.
# Fix #74 — MANUAL_REVIEW added: POST /confirm with decision=MANUAL_REVIEW
# was always returning 404 because ValueError was mapped to 404 in the route.
_TERMINAL_STATUSES = {"APPROVED", "REJECTED", "MANUAL_REVIEW"}
# Statuses a claim must be in to be moved
_MUTABLE_FROM      = {"AWAITING_INPUT", "MANUAL_REVIEW"}


class ClaimStateConflictError(Exception):
    """Raised when the current status doesn't allow the requested transition."""


@write_restricted
async def update_claim_status(
    claim_id:    int,
    new_status:  str,
    actor:       str,
    note:        str,
) -> dict[str, Any]:
    """Move a claim to APPROVED or REJECTED with an audit trail.

    Args:
        claim_id:   Primary key of the claim row.
        new_status: "APPROVED" or "REJECTED".
        actor:      Who performed the override (will come from JWT in C1.4).
        note:       Free-text justification appended to ai_reasoning.

    Returns:
        Persisted claim row as a dict (same shape as get_claim_by_id).

    Raises:
        ValueError:             If new_status is not APPROVED / REJECTED,
                                or if the claim doesn't exist.
        ClaimStateConflictError: If the claim is in a state that can't
                                  be updated (already APPROVED / REJECTED).
        RuntimeError:           DB query failure.
    """
    new_status = new_status.upper()
    if new_status not in _TERMINAL_STATUSES:
        raise ValueError(
            f"new_status must be one of {sorted(_TERMINAL_STATUSES)}, got {new_status!r}"
        )

    logger.info(
        f"update_claim_status called | claim_id={claim_id} "
        f"new_status={new_status} actor={actor}"
    )

    audit_line = (
        f"\n\n[MANUAL OVERRIDE — {datetime.now(timezone.utc).isoformat()} "
        f"by {actor}] Status set to {new_status}. Reason: {note}"
    )

    try:
        engine = get_engine()
        with engine.begin() as conn:
            # 1. Lock the row + read current status atomically
            current = conn.execute(
                text("""
                    SELECT claim_status::text AS claim_status, ai_reasoning
                    FROM   claims
                    WHERE  claim_id = :claim_id
                    FOR UPDATE
                """),
                {"claim_id": claim_id},
            ).fetchone()

            if current is None:
                raise ValueError(f"Claim {claim_id} not found")

            if str(current.claim_status) not in _MUTABLE_FROM:
                raise ClaimStateConflictError(
                    f"Claim {claim_id} is in state {current.claim_status}; "
                    f"manual update only allowed from {sorted(_MUTABLE_FROM)}"
                )

            # 2. Apply the transition + append to ai_reasoning
            result = conn.execute(
                text("""
                    UPDATE claims
                    SET    claim_status = CAST(:new_status AS claim_status),
                           ai_reasoning = ai_reasoning || :audit_line
                    WHERE  claim_id     = :claim_id
                    RETURNING claim_id, user_id, policy_id, incident_date,
                              incident_description, incident_location,
                              CAST(severity_label AS text) AS severity_label,
                              reported_damage_amount,
                              CAST(claim_status   AS text) AS claim_status,
                              ai_reasoning,
                              created_at, updated_at
                """),
                {
                    "claim_id":   claim_id,
                    "new_status": new_status,
                    "audit_line": audit_line,
                },
            )
            row = result.fetchone()

    except (ValueError, ClaimStateConflictError):
        raise
    except Exception as e:
        logger.error(f"update_claim_status failed | claim_id={claim_id} error={e}")
        raise RuntimeError(f"Failed to update claim_id={claim_id}: {e}") from e

    persisted = {
        "claim_id":               int(row.claim_id),
        "user_id":                int(row.user_id),
        "policy_id":              int(row.policy_id),
        "incident_date":          row.incident_date.isoformat(),
        "incident_description":   str(row.incident_description),
        "incident_location":      str(row.incident_location) if row.incident_location else None,
        "severity_label":         str(row.severity_label),
        "reported_damage_amount": str(row.reported_damage_amount) if row.reported_damage_amount is not None else None,
        "claim_status":           str(row.claim_status),
        "ai_reasoning":           str(row.ai_reasoning),
        "created_at":             row.created_at.isoformat() if row.created_at else None,
        "updated_at":             row.updated_at.isoformat() if row.updated_at else None,
    }
    logger.info(
        f"update_claim_status done | claim_id={claim_id} "
        f"new_status={new_status} updated_at={persisted['updated_at']}"
    )
    return persisted
