"""Coverage Rules Engine — B2.1 (official backlog)

Pure-Python, deterministic business-rule engine that turns a TriageReport
into a coverage recommendation. The auditor_agent calls this engine via a
LangChain @tool wrapper (core/tools/reasoning.py) so the LLM never has to
"eyeball" the flags — the rules live in code where they are testable and
auditable (Rule 4 / Rule 11).

Public API:
    evaluate_coverage(triage_report) -> CoverageEvaluation
    find_applicable_policy(policies, incident_date) -> dict | None

Rules applied:
    Rule 2  — Monetary fields kept as Decimal in policy data, never float
    Rule 11 — Type hints + dataclasses; no raw dict in/out of internal helpers
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ── enums for deterministic outputs ───────────────────────────────

class CoverageDecision(str, Enum):
    """Recommended decision from the rules engine."""
    APPROVED      = "APPROVED"
    REJECTED      = "REJECTED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class ReasonCode(str, Enum):
    """Machine-readable reason for the recommended decision."""
    EXTRACTION_FAILED         = "extraction_failed"
    USER_NOT_FOUND            = "user_not_found"
    MISSING_INCIDENT_DATE     = "missing_incident_date"

    POLICY_EXPIRED            = "policy_expired"
    POLICY_INACTIVE           = "policy_inactive"
    NO_ACTIVE_POLICY          = "no_active_policy"
    INCIDENT_BEFORE_POLICY    = "incident_before_policy"
    USER_MISMATCH             = "user_mismatch"

    AMOUNT_EXCEEDS_COVERAGE   = "amount_exceeds_coverage"
    MISSING_DAMAGE_AMOUNT     = "missing_damage_amount"
    HAS_WARNING_FLAGS         = "has_warning_flags"

    APPROVED_ACTIVE_COVERAGE  = "approved_active_coverage"


# ── output dataclass ─────────────────────────────────────────────

@dataclass(frozen=True)
class CoverageEvaluation:
    """Deterministic recommendation produced by the rules engine."""
    decision:             CoverageDecision
    reason_code:          ReasonCode
    details:              str
    applicable_policy_id: int | None = None


# ── helpers ──────────────────────────────────────────────────────

def _parse_date(value: Any) -> date | None:
    """Best-effort parse of a date — accepts ISO string or date object."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _parse_decimal(value: Any) -> Decimal | None:
    """Best-effort parse of a Decimal monetary value."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _critical_flags(flags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [f for f in flags if isinstance(f, dict) and f.get("severity") == "critical"]


def _warning_flags(flags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [f for f in flags if isinstance(f, dict) and f.get("severity") == "warning"]


def _pick_fallback_policy_id(policies: list[dict[str, Any]]) -> int | None:
    """Pick a policy_id for rejection records when no policy actually covers
    the incident — prefer the most recently expired one, else the first.
    """
    if not policies:
        return None
    # Sort by end_date desc; the most recent policy is the best label
    def _key(p: dict[str, Any]) -> str:
        return str(p.get("end_date", ""))
    sorted_p = sorted(policies, key=_key, reverse=True)
    return sorted_p[0].get("policy_id")


# ── public engine ────────────────────────────────────────────────

def find_applicable_policy(
    policies: list[dict[str, Any]],
    incident_date: date,
) -> dict[str, Any] | None:
    """Return the policy that is ACTIVE and covers the incident_date."""
    for p in policies:
        if not isinstance(p, dict):
            continue
        if str(p.get("status", "")).upper() != "ACTIVE":
            continue
        start = _parse_date(p.get("start_date"))
        end   = _parse_date(p.get("end_date"))
        if start is None or end is None:
            continue
        if start <= incident_date <= end:
            return p
    return None


def evaluate_coverage(triage_report: dict[str, Any]) -> CoverageEvaluation:
    """Apply business rules to a TriageReport and produce a recommendation.

    Order of evaluation matters — earlier rules short-circuit later ones.

    Args:
        triage_report: Output of analyst_agent (TriageReport as dict).

    Returns:
        CoverageEvaluation with decision, reason_code, details, and the
        most relevant policy_id (may be the expired one for REJECTED).
    """
    # ── 1. Pipeline-level failures ────────────────────────────────
    status = str(triage_report.get("status", "")).upper()
    if status == "EXTRACTION_FAILED":
        return CoverageEvaluation(
            decision=CoverageDecision.MANUAL_REVIEW,
            reason_code=ReasonCode.EXTRACTION_FAILED,
            details="Analyst could not extract claim data from the PDF; human review required.",
        )
    if status == "USER_NOT_FOUND":
        return CoverageEvaluation(
            decision=CoverageDecision.REJECTED,
            reason_code=ReasonCode.USER_NOT_FOUND,
            details="The policyholder ID in the claim does not exist in the legacy database.",
        )

    # ── 2. Pull data ──────────────────────────────────────────────
    claim    = triage_report.get("claim")    or {}
    policies = triage_report.get("policies") or []
    flags    = triage_report.get("flags")    or []

    incident_date = _parse_date(claim.get("incident_date"))

    # ── 3. Critical analyst flags short-circuit to REJECTED ───────
    critical = _critical_flags(flags)
    if critical:
        code = critical[0].get("code", "policy_expired")
        try:
            reason = ReasonCode(code)
        except ValueError:
            reason = ReasonCode.POLICY_EXPIRED
        return CoverageEvaluation(
            decision=CoverageDecision.REJECTED,
            reason_code=reason,
            details=(
                f"Analyst flagged {len(critical)} critical issue(s): "
                + "; ".join(f.get("message", "") for f in critical)
            ),
            applicable_policy_id=_pick_fallback_policy_id(policies),
        )

    # ── 4. Need an incident date to evaluate further ──────────────
    if incident_date is None:
        return CoverageEvaluation(
            decision=CoverageDecision.MANUAL_REVIEW,
            reason_code=ReasonCode.MISSING_INCIDENT_DATE,
            details="Incident date is missing or invalid; human review required.",
            applicable_policy_id=_pick_fallback_policy_id(policies),
        )

    # ── 5. Coverage check ─────────────────────────────────────────
    if not policies:
        return CoverageEvaluation(
            decision=CoverageDecision.REJECTED,
            reason_code=ReasonCode.NO_ACTIVE_POLICY,
            details="User has no policies on record.",
        )

    applicable = find_applicable_policy(policies, incident_date)
    if applicable is None:
        return CoverageEvaluation(
            decision=CoverageDecision.REJECTED,
            reason_code=ReasonCode.POLICY_EXPIRED,
            details=(
                f"No ACTIVE policy covers incident date {incident_date.isoformat()}. "
                "The user's policies either expired, are not yet in force, or are inactive."
            ),
            applicable_policy_id=_pick_fallback_policy_id(policies),
        )

    # ── 6. Amount vs. coverage limit ──────────────────────────────
    amount   = _parse_decimal(claim.get("reported_damage_amount"))
    coverage = _parse_decimal(applicable.get("coverage_limit_usd"))
    if amount is not None and coverage is not None and amount > coverage:
        return CoverageEvaluation(
            decision=CoverageDecision.MANUAL_REVIEW,
            reason_code=ReasonCode.AMOUNT_EXCEEDS_COVERAGE,
            details=(
                f"Reported damage {amount} USD exceeds policy coverage limit "
                f"{coverage} USD; human review required."
            ),
            applicable_policy_id=applicable.get("policy_id"),
        )

    # ── 7. Warning flags escalate to MANUAL_REVIEW ────────────────
    warnings = _warning_flags(flags)
    if warnings:
        return CoverageEvaluation(
            decision=CoverageDecision.MANUAL_REVIEW,
            reason_code=ReasonCode.HAS_WARNING_FLAGS,
            details=(
                f"Active policy covers the incident, but analyst raised "
                f"warning flag(s): {[f.get('code') for f in warnings]}. "
                "Human review required."
            ),
            applicable_policy_id=applicable.get("policy_id"),
        )

    # ── 8. Clean approve ─────────────────────────────────────────
    return CoverageEvaluation(
        decision=CoverageDecision.APPROVED,
        reason_code=ReasonCode.APPROVED_ACTIVE_COVERAGE,
        details=(
            f"Policy {applicable.get('policy_id')} is ACTIVE and covers the "
            f"incident date {incident_date.isoformat()}. No critical or warning "
            f"flags from analyst."
        ),
        applicable_policy_id=applicable.get("policy_id"),
    )
