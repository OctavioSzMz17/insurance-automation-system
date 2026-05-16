"""LangChain @tool wrapper for the Claims MCP server.

Exposes the write-restricted create_claim tool to the auditor_agent.
Phase B uses direct in-process calls; Phase C swaps to SSE.

Rules applied:
    Rule 3  — Write-restricted; only the auditor (WRITE role) may call this
    Rule 11 — Type hints; Pydantic-validated input via the underlying tool
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from ._loader import load_mcp_tool

logger = logging.getLogger(__name__)

# Bind the underlying MCP tool function once at import time
_create_claim = load_mcp_tool("claims", "create_claim")


@tool
async def create_claim(
    user_id: int,
    policy_id: int,
    incident_date: str,
    incident_description: str,
    severity_label: str,
    claim_status: str,
    ai_reasoning: str,
    incident_location: str | None = None,
    reported_damage_amount: str | None = None,
) -> dict[str, Any]:
    """Persist a final claim decision into the database.

    WRITE-RESTRICTED — only the auditor_agent should call this. Use this
    AFTER you have decided the outcome (APPROVED / REJECTED / MANUAL_REVIEW).

    Args:
        user_id:               Policyholder integer ID.
        policy_id:             Integer ID of the affected policy.
        incident_date:         ISO-8601 date string (YYYY-MM-DD).
        incident_description:  Free-text description of the incident.
        severity_label:        One of LOW | HIGH | TOTAL_LOSS.
        claim_status:          Final decision: APPROVED | REJECTED | MANUAL_REVIEW.
        ai_reasoning:          Detailed explanation of WHY this decision was reached.
        incident_location:     Optional location string.
        reported_damage_amount: Optional monetary amount as a string (e.g. "1234.56").
    """
    logger.info(
        f"@tool create_claim | user_id={user_id} policy_id={policy_id} "
        f"claim_status={claim_status}"
    )
    return await _create_claim({
        "user_id":                user_id,
        "policy_id":              policy_id,
        "incident_date":          incident_date,
        "incident_description":   incident_description,
        "incident_location":      incident_location,
        "severity_label":         severity_label,
        "reported_damage_amount": reported_damage_amount,
        "claim_status":           claim_status,
        "ai_reasoning":           ai_reasoning,
    })


CLAIMS_TOOLS = [create_claim]
