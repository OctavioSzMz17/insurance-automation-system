"""Validators for the analyst_agent's TriageReport output (B2.4).

Each function checks one rule and returns a list of ConstraintViolation
(empty = passed). validate_triage_report() runs all of them.

Rules applied:
    Rule 1  — Validates against TriageReport Pydantic schema (packages/common)
    Rule 5  — Returns structured violations the pipeline can feed back to the agent
    Rule 11 — Type hints throughout
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Make packages/common importable
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from pydantic import ValidationError as PydanticValidationError  # noqa: E402

from packages.common.schemas.agent import TriageReport  # noqa: E402

from ..models import ConstraintViolation  # noqa: E402


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
        return [_violation(
            field="<root>",
            rule="must_be_dict",
            value=type(output).__name__,
            expected="JSON object",
        )]
    return []


def check_pydantic_schema(output: dict) -> list[ConstraintViolation]:
    """Run the TriageReport Pydantic schema; convert errors to ConstraintViolation."""
    try:
        TriageReport(**output)
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


def check_extracted_consistency(output: dict) -> list[ConstraintViolation]:
    """If status==EXTRACTED, claim and user must be present."""
    if output.get("status") != "EXTRACTED":
        return []
    violations: list[ConstraintViolation] = []
    if not output.get("claim"):
        violations.append(_violation(
            field="claim",
            rule="required_when_extracted",
            value=output.get("claim"),
            expected="non-null dict when status='EXTRACTED'",
        ))
    if not output.get("user"):
        violations.append(_violation(
            field="user",
            rule="required_when_extracted",
            value=output.get("user"),
            expected="non-null dict when status='EXTRACTED'",
        ))
    return violations


def check_unique_policy_ids(output: dict) -> list[ConstraintViolation]:
    """Each policy in the list must have a unique policy_id."""
    policies = output.get("policies") or []
    if not isinstance(policies, list):
        return []
    ids = [p.get("policy_id") for p in policies if isinstance(p, dict)]
    if len(ids) != len(set(ids)):
        return [_violation(
            field="policies",
            rule="unique_policy_ids",
            value=ids,
            expected="all policy_id values must be unique",
        )]
    return []


# ── public entry point ───────────────────────────────────────────

def validate_triage_report(output: Any) -> list[ConstraintViolation]:
    """Run every TriageReport validator. Return aggregated violations."""
    violations = check_is_dict(output)
    if violations:
        # Can't run further checks if it's not even a dict
        return violations

    violations.extend(check_pydantic_schema(output))
    violations.extend(check_extracted_consistency(output))
    violations.extend(check_unique_policy_ids(output))
    return violations
