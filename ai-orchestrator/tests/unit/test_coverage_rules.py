"""Unit tests for the Coverage Rules Engine — B4.1

Pure-Python tests against agents/reasoning/coverage_rules.py.
No LLM, no DB, no network. Deterministic and fast.

Run:
    py -m pytest ai-orchestrator/tests/unit/test_coverage_rules.py -v
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

# Make agents.reasoning.coverage_rules importable
_ORCH_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ORCH_ROOT / "agents"))

from reasoning.coverage_rules import (  # noqa: E402
    CoverageDecision,
    CoverageEvaluation,
    ReasonCode,
    evaluate_coverage,
    find_applicable_policy,
)


# ── fixtures (builders) ───────────────────────────────────────────

def _claim(
    *,
    incident_date: str = "2025-06-15",
    amount:        str | None = None,
) -> dict[str, Any]:
    return {
        "claim_id":               "CASE-2025-0001",
        "user_id":                101,
        "insured_name":           "Juan Pérez",
        "incident_date":          incident_date,
        "description":            "Test incident description.",
        "location":               "Test Location",
        "damage_summary":         "Test damage.",
        "reported_damage_amount": amount,
    }


def _active_policy(
    *,
    policy_id:  int = 1,
    start:      str = "2025-01-01",
    end:        str = "2026-01-01",
    coverage:   str = "50000.00",
    deductible: str = "500.00",
    status:     str = "ACTIVE",
) -> dict[str, Any]:
    return {
        "policy_id":          policy_id,
        "user_id":            101,
        "type":               "Premium Full Coverage",
        "status":             status,
        "deductible_usd":     deductible,
        "coverage_limit_usd": coverage,
        "start_date":         start,
        "end_date":           end,
    }


def _report(
    *,
    status:   str = "EXTRACTED",
    claim:    dict[str, Any] | None = None,
    policies: list[dict[str, Any]] | None = None,
    flags:    list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "status":   status,
        "claim":    claim    if claim    is not None else _claim(),
        "user":     {"user_id": 101, "name": "Juan", "email": "j@e.com"},
        "policies": policies if policies is not None else [_active_policy()],
        "flags":    flags    or [],
        "summary":  "test",
    }


def _flag(code: str, severity: str) -> dict[str, Any]:
    return {"code": code, "message": f"{code} occurred", "severity": severity}


# ── find_applicable_policy ────────────────────────────────────────

class TestFindApplicablePolicy:
    def test_returns_policy_when_active_and_in_range(self) -> None:
        policy = _active_policy(start="2025-01-01", end="2026-01-01")
        result = find_applicable_policy([policy], date(2025, 6, 15))
        assert result is policy

    def test_returns_none_when_no_policies(self) -> None:
        assert find_applicable_policy([], date(2025, 6, 15)) is None

    def test_skips_inactive_policies(self) -> None:
        policy = _active_policy(status="EXPIRED")
        assert find_applicable_policy([policy], date(2025, 6, 15)) is None

    def test_skips_out_of_range_policies(self) -> None:
        policy = _active_policy(start="2020-01-01", end="2021-01-01")
        assert find_applicable_policy([policy], date(2025, 6, 15)) is None

    def test_inclusive_boundaries(self) -> None:
        policy = _active_policy(start="2025-06-15", end="2025-06-15")
        assert find_applicable_policy([policy], date(2025, 6, 15)) is policy

    def test_picks_active_policy_among_many(self) -> None:
        expired = _active_policy(policy_id=1, status="EXPIRED")
        active  = _active_policy(policy_id=2, start="2025-01-01", end="2026-01-01")
        result  = find_applicable_policy([expired, active], date(2025, 6, 15))
        assert result is active


# ── evaluate_coverage — pipeline-level failure paths ──────────────

class TestPipelineFailures:
    def test_extraction_failed_routes_to_manual_review(self) -> None:
        result = evaluate_coverage(_report(status="EXTRACTION_FAILED"))
        assert result.decision    == CoverageDecision.MANUAL_REVIEW
        assert result.reason_code == ReasonCode.EXTRACTION_FAILED

    def test_user_not_found_routes_to_rejected(self) -> None:
        result = evaluate_coverage(_report(status="USER_NOT_FOUND"))
        assert result.decision    == CoverageDecision.REJECTED
        assert result.reason_code == ReasonCode.USER_NOT_FOUND


# ── evaluate_coverage — critical flag short-circuit ───────────────

class TestCriticalFlagShortCircuit:
    def test_critical_policy_expired_rejects(self) -> None:
        report = _report(flags=[_flag("policy_expired", "critical")])
        result = evaluate_coverage(report)
        assert result.decision    == CoverageDecision.REJECTED
        assert result.reason_code == ReasonCode.POLICY_EXPIRED

    def test_critical_no_active_policy_rejects(self) -> None:
        report = _report(flags=[_flag("no_active_policy", "critical")])
        result = evaluate_coverage(report)
        assert result.decision    == CoverageDecision.REJECTED
        assert result.reason_code == ReasonCode.NO_ACTIVE_POLICY

    def test_critical_flag_sets_applicable_policy_id(self) -> None:
        """Even rejected claims should reference the policy they relate to."""
        report = _report(flags=[_flag("policy_expired", "critical")])
        result = evaluate_coverage(report)
        assert result.applicable_policy_id == 1

    def test_unknown_critical_code_falls_back_to_policy_expired(self) -> None:
        report = _report(flags=[_flag("some_made_up_code", "critical")])
        result = evaluate_coverage(report)
        assert result.decision    == CoverageDecision.REJECTED
        assert result.reason_code == ReasonCode.POLICY_EXPIRED


# ── evaluate_coverage — coverage checks ──────────────────────────

class TestCoverageChecks:
    def test_clean_approve(self) -> None:
        """Active policy + no flags + amount under limit → APPROVED."""
        report = _report(
            claim=_claim(incident_date="2025-06-15", amount="1000.00"),
            policies=[_active_policy(coverage="50000.00")],
            flags=[],
        )
        result = evaluate_coverage(report)
        assert result.decision             == CoverageDecision.APPROVED
        assert result.reason_code          == ReasonCode.APPROVED_ACTIVE_COVERAGE
        assert result.applicable_policy_id == 1

    def test_no_policies_rejects(self) -> None:
        report = _report(policies=[])
        result = evaluate_coverage(report)
        assert result.decision    == CoverageDecision.REJECTED
        assert result.reason_code == ReasonCode.NO_ACTIVE_POLICY

    def test_only_expired_policies_rejects_with_fallback_id(self) -> None:
        old = _active_policy(policy_id=7, status="EXPIRED", end="2024-01-01")
        report = _report(claim=_claim(incident_date="2025-06-15"), policies=[old])
        result = evaluate_coverage(report)
        assert result.decision             == CoverageDecision.REJECTED
        assert result.reason_code          == ReasonCode.POLICY_EXPIRED
        assert result.applicable_policy_id == 7

    def test_missing_incident_date_manual_review(self) -> None:
        report = _report(claim=_claim(incident_date=""))
        result = evaluate_coverage(report)
        assert result.decision    == CoverageDecision.MANUAL_REVIEW
        assert result.reason_code == ReasonCode.MISSING_INCIDENT_DATE

    def test_amount_exceeds_coverage_manual_review(self) -> None:
        report = _report(
            claim=_claim(amount="99999.99"),
            policies=[_active_policy(coverage="50000.00")],
        )
        result = evaluate_coverage(report)
        assert result.decision    == CoverageDecision.MANUAL_REVIEW
        assert result.reason_code == ReasonCode.AMOUNT_EXCEEDS_COVERAGE

    def test_amount_at_coverage_limit_still_approves(self) -> None:
        report = _report(
            claim=_claim(amount="50000.00"),
            policies=[_active_policy(coverage="50000.00")],
        )
        result = evaluate_coverage(report)
        assert result.decision == CoverageDecision.APPROVED


# ── evaluate_coverage — warning flag handling ────────────────────

class TestWarningFlagHandling:
    def test_warning_flag_alone_escalates_to_manual_review(self) -> None:
        report = _report(flags=[_flag("missing_damage_amount", "warning")])
        result = evaluate_coverage(report)
        assert result.decision    == CoverageDecision.MANUAL_REVIEW
        assert result.reason_code == ReasonCode.HAS_WARNING_FLAGS

    def test_info_flag_alone_still_approves(self) -> None:
        report = _report(flags=[_flag("missing_location", "info")])
        result = evaluate_coverage(report)
        assert result.decision == CoverageDecision.APPROVED

    def test_warning_plus_critical_critical_wins(self) -> None:
        """Critical flag takes precedence over warning."""
        report = _report(flags=[
            _flag("missing_damage_amount", "warning"),
            _flag("policy_expired", "critical"),
        ])
        result = evaluate_coverage(report)
        assert result.decision == CoverageDecision.REJECTED


# ── evaluate_coverage — multi-policy selection ───────────────────

class TestMultiPolicySelection:
    def test_picks_policy_covering_incident_date(self) -> None:
        old_expired = _active_policy(policy_id=1, status="EXPIRED",
                                     start="2024-01-01", end="2025-01-01")
        new_active  = _active_policy(policy_id=2, status="ACTIVE",
                                     start="2025-01-01", end="2026-01-01")
        report = _report(
            claim=_claim(incident_date="2025-06-15"),
            policies=[old_expired, new_active],
        )
        result = evaluate_coverage(report)
        assert result.decision             == CoverageDecision.APPROVED
        assert result.applicable_policy_id == 2

    def test_no_policy_covers_incident_rejects(self) -> None:
        """User has policies but none cover the incident date."""
        old = _active_policy(policy_id=1, status="ACTIVE",
                             start="2024-01-01", end="2025-01-01")
        report = _report(
            claim=_claim(incident_date="2026-06-15"),
            policies=[old],
        )
        result = evaluate_coverage(report)
        assert result.decision    == CoverageDecision.REJECTED
        assert result.reason_code == ReasonCode.POLICY_EXPIRED


# ── return type ──────────────────────────────────────────────────

class TestReturnType:
    def test_evaluate_coverage_returns_dataclass(self) -> None:
        result = evaluate_coverage(_report())
        assert isinstance(result, CoverageEvaluation)

    def test_details_is_non_empty(self) -> None:
        result = evaluate_coverage(_report(flags=[_flag("policy_expired", "critical")]))
        assert isinstance(result.details, str)
        assert len(result.details) > 0
