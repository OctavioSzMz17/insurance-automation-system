"""LangChain @tool wrapper for the Coverage Rules Engine — B2.1.

Exposes the deterministic Python rules engine
(agents/reasoning/coverage_rules.py) as a LangChain tool so the
auditor_agent can call it through its normal tool-use loop.

Why a tool, not an internal call?
    Keeps the auditor inside the LangChain ecosystem (one execution
    model: tool calls). The agent's reasoning_trail records the call
    just like any other tool invocation. Swappable / observable.

Rules applied:
    Rule 4  — Auditor still makes the final call, but with a deterministic
              recommendation in hand (no hallucinated coverage logic)
    Rule 11 — Type hints throughout
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

# Make agents/reasoning/coverage_rules importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents"))

from reasoning.coverage_rules import evaluate_coverage  # noqa: E402

logger = logging.getLogger(__name__)


@tool
async def evaluate_coverage_rules(triage_report: dict[str, Any]) -> dict[str, Any]:
    """Apply deterministic coverage rules to a TriageReport.

    USE THIS BEFORE making the final decision. It is the single source of
    truth for coverage logic — never try to apply these rules yourself.

    Returns a dict with:
        decision              : "APPROVED" | "REJECTED" | "MANUAL_REVIEW"
        reason_code           : machine code, e.g. "policy_expired"
        details               : human-readable explanation
        applicable_policy_id  : the most relevant policy_id (or null)

    Args:
        triage_report: The full TriageReport dict produced by the analyst.
    """
    logger.info(
        f"@tool evaluate_coverage_rules | flags="
        f"{[f.get('code') for f in (triage_report.get('flags') or [])]}"
    )
    result = evaluate_coverage(triage_report)
    return {
        "decision":             result.decision.value,
        "reason_code":          result.reason_code.value,
        "details":              result.details,
        "applicable_policy_id": result.applicable_policy_id,
    }


REASONING_TOOLS = [evaluate_coverage_rules]
