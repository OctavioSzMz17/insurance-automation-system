"""Tool: create_claim — B2.3

Persists an auditor-produced ClaimDecision into the PostgreSQL claims
table. This is the ONLY write performed by any agent in the system.

Input is validated through ClaimCreateSchema (Rule 11). The DB layer
enforces the last line of defense via NUMERIC types, FK constraints,
and CHECK constraints.

Rules applied:
    Rule 2  — Monetary fields as Decimal, never float
    Rule 3  — @write_restricted decorator (Rule enforcement at C1.5)
    Rule 4  — ai_reasoning required (validated by ClaimCreateSchema)
    Rule 9  — Structured logging; no print()
    Rule 11 — Full type hints; Pydantic-validated input
"""

from __future__ import annotations

import logging
import sys
from datetime import date as date_type
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text

# Allow direct execution / Claude Desktop / loader-based import
sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parents[3]))

from db import get_engine                                    # noqa: E402
from packages.common.schemas.claim import ClaimCreateSchema  # noqa: E402

# Master Patch Part 3 — trace-marker stamping is now the orchestrator's
# responsibility (signed with HMAC-SHA256 via packages.common.trace_signing).
# The legacy unsigned-marker prepended here was forgeable by any caller
# of this MCP and has been removed. The orchestrator pre-prepends a
# signed marker to `ai_reasoning` before it crosses the MCP boundary.

# Import write_restricted via direct file load so this module works both
# as a package (mcp server entry point) and as a standalone module
# (when loaded by ai-orchestrator/core/tools/_loader.py).
import importlib.util as _ilu
_marker_path = Path(__file__).parent / "__init__.py"
_spec = _ilu.spec_from_file_location("_claims_tools_init", _marker_path)
_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
write_restricted = _mod.write_restricted

logger = logging.getLogger(__name__)


@write_restricted
async def create_claim(claim_data: dict[str, Any]) -> dict[str, Any]:
    """Insert a new claim record. Returns the persisted row as a dict.

    Args:
        claim_data: Plain dict that will be validated via ClaimCreateSchema.
                    Required keys: user_id, policy_id, incident_date,
                    incident_description, severity_label, claim_status,
                    ai_reasoning.

    Returns:
        Persisted claim row as a dict (claim_id assigned by DB).

    Raises:
        ValueError:   If claim_data fails Pydantic validation.
        RuntimeError: If the INSERT fails (FK violation, etc.).
    """
    logger.info(
        f"create_claim called | user_id={claim_data.get('user_id')} "
        f"policy_id={claim_data.get('policy_id')} "
        f"claim_status={claim_data.get('claim_status')}"
    )

    # Layer 1: format validation (Rule 11)
    try:
        validated = ClaimCreateSchema(**claim_data)
    except Exception as e:
        logger.error(f"create_claim validation failed | error={e}")
        raise ValueError(f"Invalid claim_data: {e}") from e

    # Master Patch Part 3 — no marker stamping here. The orchestrator
    # pre-prepends a signed [trace_id: TID.SIG] marker to ai_reasoning
    # before calling this tool. Whatever validated.ai_reasoning contains
    # is what gets persisted, unchanged.
    ai_reasoning_to_persist = validated.ai_reasoning

    # Layer 2: write (DB constraints + triggers enforce final guarantees)
    try:
        engine = get_engine()
        with engine.begin() as conn:
            result = conn.execute(
                text("""
                    INSERT INTO claims (
                        user_id, policy_id, incident_date,
                        incident_description, incident_location,
                        severity_label, reported_damage_amount,
                        claim_status, ai_reasoning
                    )
                    VALUES (
                        :user_id, :policy_id, :incident_date,
                        :incident_description, :incident_location,
                        :severity_label, :reported_damage_amount,
                        :claim_status, :ai_reasoning
                    )
                    RETURNING claim_id, user_id, policy_id, incident_date,
                              incident_description, incident_location,
                              severity_label::text, reported_damage_amount,
                              claim_status::text, ai_reasoning,
                              created_at, updated_at
                """),
                {
                    "user_id":                validated.user_id,
                    "policy_id":              validated.policy_id,
                    "incident_date":          validated.incident_date,
                    "incident_description":   validated.incident_description,
                    "incident_location":      validated.incident_location,
                    "severity_label":         validated.severity_label.value,
                    "reported_damage_amount": validated.reported_damage_amount,
                    "claim_status":           validated.claim_status.value,
                    "ai_reasoning":           ai_reasoning_to_persist,
                },
            )
            row = result.fetchone()

    except Exception as e:
        logger.error(f"create_claim DB write failed | error={e}")
        raise RuntimeError(f"Failed to persist claim: {e}") from e

    persisted = {
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
    logger.info(f"create_claim persisted | claim_id={persisted['claim_id']}")
    return persisted
