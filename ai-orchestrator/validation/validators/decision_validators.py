"""Validators for the auditor_agent's ClaimDecision output (B2.4).

These validators are CROSS-REFERENCE aware: they need both the decision
and the TriageReport (so they can verify the decision didn't make up data).

Rules applied:
    Rule 1  — Validates against ClaimDecision Pydantic schema (packages/common)
    Rule 4  — Enforces non-empty ai_reasoning
    Rule 5  — Returns structured violations for self-healing retry
    Rule 11 — Type hints throughout
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from pydantic import ValidationError as PydanticValidationError  # noqa: E402

from packages.common.schemas.agent import ClaimDecision  # noqa: E402

from ..models import ConstraintViolation  # noqa: E402

# Minimum length for the auditor's ai_reasoning — Rule 4 wants real reasoning
_MIN_REASONING_CHARS = 40


def _violation(field: str, rule: str, value: Any, expected: str) -> ConstraintViolation:
    return ConstraintViolation(
        field=field,
        rule=rule,
        actual_value=str(value)[:200],
        expected_constraint=expected,
        severity="error",
    )


# ── individual checks ─────────────────────────────────────────────

def check_is_dict(output: Any) -> list[ConstraintViolation]:
    if not isinstance(output, dict):
        return [_violation("<root>", "must_be_dict", type(output).__name__, "JSON object")]
    return []


def check_pydantic_schema(output: dict) -> list[ConstraintViolation]:
    try:
        ClaimDecision(**output)
        return []
    except PydanticValidationError as e:
        violations: list[ConstraintViolation] = []
        for err in e.errors():
            loc = ".".join(str(p) for p in err.get("loc", []))
            violations.append(_violation(
                field=loc or "<root>",
                rule=err.get("type", "schema_violation"),
                value=err.get("input"),
                expected=err.get("msg", "valid value"),
            ))
        return violations


def check_reasoning_quality(output: dict) -> list[ConstraintViolation]:
    """Rule 4: ai_reasoning must be a real paragraph, not a placeholder."""
    reasoning = output.get("ai_reasoning") or ""
    if len(reasoning.strip()) < _MIN_REASONING_CHARS:
        return [_violation(
            field="ai_reasoning",
            rule="min_length",
            value=f"len={len(reasoning)}",
            expected=f"at least {_MIN_REASONING_CHARS} characters (Rule 4)",
        )]
    return []


def check_policy_id_belongs_to_user(
    output: dict, triage_report: dict | None
) -> list[ConstraintViolation]:
    """The chosen policy_id must exist in the TriageReport's policies list.

    Prevents the LLM from hallucinating a policy_id the user doesn't own.
    """
    if not triage_report:
        return []  # nothing to cross-reference
    chosen = output.get("policy_id")
    if chosen is None:
        return []  # null policy_id is handled by the schema + prompt

    policies = triage_report.get("policies") or []
    known_ids = {p.get("policy_id") for p in policies if isinstance(p, dict)}
    if chosen not in known_ids:
        return [_violation(
            field="policy_id",
            rule="must_belong_to_user",
            value=chosen,
            expected=f"one of {sorted(i for i in known_ids if i is not None)}",
        )]
    return []


def check_decision_consistency(
    output: dict, triage_report: dict | None
) -> list[ConstraintViolation]:
    """APPROVED is incompatible with critical flags."""
    if not triage_report:
        return []
    if output.get("decision") != "APPROVED":
        return []
    flags = triage_report.get("flags") or []
    critical = [f for f in flags if isinstance(f, dict) and f.get("severity") == "critical"]
    if critical:
        return [_violation(
            field="decision",
            rule="approved_requires_no_critical_flags",
            value=output.get("decision"),
            expected=(
                f"REJECTED or MANUAL_REVIEW because critical flags exist: "
                f"{[f.get('code') for f in critical]}"
            ),
        )]
    return []


def check_persistence_happened(output: dict) -> list[ConstraintViolation]:
    """If the decision is APPROVED or REJECTED, the auditor must have actually
    called create_claim. The agent code overwrites persisted_claim_id with the
    real tool result (or None if never called), so a null here means the LLM
    hallucinated its answer without persisting.
    """
    decision = output.get("decision")
    if decision not in ("APPROVED", "REJECTED"):
        return []  # MANUAL_REVIEW is allowed without persistence
    if output.get("persisted_claim_id") is None:
        return [_violation(
            field="persisted_claim_id",
            rule="must_call_create_claim",
            value=None,
            expected=(
                "an integer claim_id returned by create_claim — you MUST "
                "actually call the create_claim tool before returning a final "
                f"decision of {decision!r}"
            ),
        )]
    return []


def check_next_action_matches_decision(output: dict) -> list[ConstraintViolation]:
    """next_action must align with decision."""
    expected_map = {
        "APPROVED":      "process_payout",
        "REJECTED":      "send_rejection_letter",
        "MANUAL_REVIEW": "escalate_to_human",
    }
    decision = output.get("decision")
    next_action = output.get("next_action")
    expected = expected_map.get(decision)
    if expected and next_action != expected:
        return [_violation(
            field="next_action",
            rule="must_match_decision",
            value=next_action,
            expected=f"'{expected}' (because decision='{decision}')",
        )]
    return []


# ── public entry point ───────────────────────────────────────────

def validate_claim_decision(
    output: Any, triage_report: dict | None = None
) -> list[ConstraintViolation]:
    """Run every ClaimDecision validator. Return aggregated violations.

    Args:
        output:         Auditor's ClaimDecision dict.
        triage_report:  Original analyst output (used for cross-reference checks).
    """
    violations = check_is_dict(output)
    if violations:
        return violations

    violations.extend(check_pydantic_schema(output))
    violations.extend(check_reasoning_quality(output))
    violations.extend(check_policy_id_belongs_to_user(output, triage_report))
    violations.extend(check_decision_consistency(output, triage_report))
    violations.extend(check_persistence_happened(output))
    violations.extend(check_next_action_matches_decision(output))
    return violations
