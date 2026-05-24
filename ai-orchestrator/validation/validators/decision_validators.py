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
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from pydantic import ValidationError as PydanticValidationError  # noqa: E402

from packages.common.schemas.agent import ClaimDecision  # noqa: E402

from ..models import ConstraintViolation  # noqa: E402

# Minimum length for the auditor's ai_reasoning — Rule 4 wants real reasoning
_MIN_REASONING_CHARS = 40

# ── Deterministic severity matrix (Master Patch Part 3) ──────────────
# Pure-code mathematical contract. The LLM cannot bypass this via prompt
# injection — if its severity_label disagrees with the amount, the
# validation pipeline issues a self-healing retry with the specific
# violation embedded in the retry message.
_SEVERITY_LOW_MAX:   Decimal = Decimal("2000")    # exclusive upper bound for LOW
_SEVERITY_HIGH_MAX:  Decimal = Decimal("15000")   # inclusive upper bound for HIGH


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


def check_severity_matches_amount(output: dict) -> list[ConstraintViolation]:
    """Deterministic severity ↔ amount contract (Master Patch Part 3).

    Closes the indirect-prompt-injection vector where a malicious PDF
    tricks the auditor LLM into emitting a downgraded `severity_label`
    (e.g. labelling a total-loss claim as LOW). The classification is
    derived in pure code from the monetary amount — the LLM has no say.

    Contract (Decimal precision, never float):
        amount < 2000                       → LOW
        2000 <= amount <= 15000             → HIGH
        amount > 15000                      → TOTAL_LOSS

    If the agent did NOT report a monetary amount, this check is a
    no-op — the orchestrator's MANUAL_REVIEW escalation handles the
    ambiguous case downstream.
    """
    raw_amount = output.get("reported_damage_amount")
    if raw_amount is None:
        return []  # nothing to anchor the contract on — skip

    # The LLM may emit Decimal, str, or int. Normalise to Decimal via
    # str() to avoid float-binary surprises (Rule 2 — never trust float).
    try:
        amount = Decimal(str(raw_amount))
    except (InvalidOperation, ValueError, TypeError):
        return [_violation(
            field="reported_damage_amount",
            rule="must_be_decimal",
            value=raw_amount,
            expected="a Decimal-parseable string/number",
        )]

    if amount < 0:
        # Existing ClaimSchema guard catches this on persistence, but we
        # want a clean retry signal here rather than a 500 downstream.
        return [_violation(
            field="reported_damage_amount",
            rule="must_be_non_negative",
            value=str(amount),
            expected=">= 0",
        )]

    if amount < _SEVERITY_LOW_MAX:
        expected = "LOW"
    elif amount <= _SEVERITY_HIGH_MAX:
        expected = "HIGH"
    else:
        expected = "TOTAL_LOSS"

    actual = output.get("severity_label")
    if actual != expected:
        return [_violation(
            field="severity_label",
            rule="severity_must_match_amount",
            value=f"{actual} (amount={amount})",
            expected=(
                f"'{expected}' — by the deterministic matrix "
                f"(<{_SEVERITY_LOW_MAX}=LOW, "
                f"{_SEVERITY_LOW_MAX}-{_SEVERITY_HIGH_MAX}=HIGH, "
                f">{_SEVERITY_HIGH_MAX}=TOTAL_LOSS)"
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
    output: Any,
    triage_report: dict | None = None,
    **_ignored: Any,
) -> list[ConstraintViolation]:
    """Run every ClaimDecision validator. Return aggregated violations.

    Args:
        output:        Auditor's TriageProposal dict (no persistence fields).
        triage_report: Original analyst output (used for cross-reference checks).

    Note: `persist` kwarg is no longer accepted — the agent never calls
    create_claim. The orchestrator handler owns all DB writes (Rule 6).
    """
    violations = check_is_dict(output)
    if violations:
        return violations

    violations.extend(check_pydantic_schema(output))
    violations.extend(check_reasoning_quality(output))
    violations.extend(check_policy_id_belongs_to_user(output, triage_report))
    violations.extend(check_decision_consistency(output, triage_report))
    violations.extend(check_next_action_matches_decision(output))
    # Master Patch Part 3 — deterministic severity matrix runs LAST so
    # earlier shape/schema violations surface first in the retry message.
    violations.extend(check_severity_matches_amount(output))
    return violations
