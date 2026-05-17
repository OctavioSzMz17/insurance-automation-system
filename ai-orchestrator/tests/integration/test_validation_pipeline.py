"""Integration tests for ValidationPipeline — B4.2

Uses MagicMock agents (no LLM cost, fully deterministic) but the REAL
validation pipeline + REAL validators. Verifies:

    1. Valid output passes on attempt 1 (no retries)
    2. Invalid output triggers retry with previous_attempt_error feedback
    3. Recovery on attempt 2/3 returns success
    4. Exhausted attempts → MANUAL_REVIEW escalation
    5. Agent raising an exception is treated as a retryable violation

Run:
    py -m pytest ai-orchestrator/tests/integration/test_validation_pipeline.py -v --asyncio-mode=auto
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

_ORCH_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ORCH_ROOT))
sys.path.insert(0, str(_ORCH_ROOT / "agents"))

from reasoning import AgentState                       # noqa: E402
from validation import (                                # noqa: E402
    ValidationPipeline,
    validate_triage_report,
    validate_claim_decision,
)


# ── helpers ────────────────────────────────────────────────────────

def _good_triage() -> dict[str, Any]:
    """A TriageReport that passes every validator."""
    return {
        "status": "EXTRACTED",
        "claim": {
            "claim_id":               "CASE-2025-0001",
            "user_id":                101,
            "insured_name":           "Juan Pérez",
            "incident_date":          "2025-06-15",
            "description":            "Test description.",
            "location":               "Test Location",
            "damage_summary":         "Test damage.",
            "reported_damage_amount": "1000.00",
        },
        "user":     {"user_id": 101, "name": "Juan", "email": "juan@test.com"},
        "policies": [{
            "policy_id":          1,
            "user_id":            101,
            "type":               "Premium",
            "status":             "ACTIVE",
            "deductible_usd":     "500.00",
            "coverage_limit_usd": "50000.00",
            "start_date":         "2025-01-01",
            "end_date":           "2026-01-01",
        }],
        "flags":   [],
        "summary": "Test claim",
    }


def _bad_triage_missing_claim() -> dict[str, Any]:
    """Status says EXTRACTED but claim is null — fails check_extracted_consistency."""
    report = _good_triage()
    report["claim"] = None
    return report


def _good_decision() -> dict[str, Any]:
    """A ClaimDecision that passes every validator."""
    return {
        "decision":              "REJECTED",
        "claim_id":              "CASE-2025-0001",
        "user_id":               101,
        "policy_id":             1,
        "severity_label":        "HIGH",
        "reported_damage_amount": None,
        "incident_date":         "2025-06-15",
        "incident_description":  "Test description.",
        "incident_location":     "Test Location",
        "ai_reasoning":          "Rejected because the policy was expired at the time of the incident — a 40+ char real reason.",
        "next_action":           "send_rejection_letter",
        "persisted_claim_id":    42,
    }


def _bad_decision_short_reasoning() -> dict[str, Any]:
    """ai_reasoning is too short → fails check_reasoning_quality."""
    bad = _good_decision()
    bad["ai_reasoning"] = "Bad."
    return bad


def _mock_agent(outputs: list[dict[str, Any] | Exception], role: str = "READ-ONLY") -> MagicMock:
    """Build a mock agent whose .run() returns each item in `outputs` in order.

    If an item is an Exception, .run() raises it.
    If an item is None, .run() returns AgentState with last_error set.
    Otherwise, .run() returns AgentState with .result = the item.
    """
    agent = MagicMock()
    agent.role = role

    call_count = {"n": 0}

    async def _run(agent_input: dict[str, Any]) -> AgentState:
        idx = min(call_count["n"], len(outputs) - 1)
        call_count["n"] += 1
        value = outputs[idx]

        if isinstance(value, Exception):
            raise value

        state = AgentState(claim_input=agent_input)
        state.iteration = 1
        if value is None:
            state.last_error = "Agent produced no result"
            state.result = None
        else:
            state.result = value
            state.is_done = True
        return state

    agent.run = AsyncMock(side_effect=_run)
    return agent


# ── happy path: passes on attempt 1 ───────────────────────────────

class TestPassesOnFirstAttempt:
    @pytest.mark.asyncio
    async def test_analyst_good_output_no_retries(self) -> None:
        agent = _mock_agent([_good_triage()])
        pipeline = ValidationPipeline(validate_triage_report, "analyst", max_attempts=3, base_delay_s=0)

        state = await pipeline.run(agent, {"pdf_path": "test.pdf"})

        assert state.is_done
        assert state.last_error is None
        assert state.retry_count == 0
        assert agent.run.call_count == 1
        assert state.result == _good_triage()

    @pytest.mark.asyncio
    async def test_auditor_good_output_no_retries(self) -> None:
        agent = _mock_agent([_good_decision()], role="READ+WRITE")
        pipeline = ValidationPipeline(validate_claim_decision, "auditor", max_attempts=3, base_delay_s=0)

        state = await pipeline.run(
            agent,
            {"triage_report": _good_triage()},
            triage_report=_good_triage(),
        )

        assert state.is_done
        assert state.retry_count == 0
        assert agent.run.call_count == 1


# ── retry path: recovers on attempt 2 ────────────────────────────

class TestRecoveryOnRetry:
    @pytest.mark.asyncio
    async def test_bad_then_good_succeeds(self) -> None:
        agent = _mock_agent([_bad_triage_missing_claim(), _good_triage()])
        pipeline = ValidationPipeline(validate_triage_report, "analyst", max_attempts=3, base_delay_s=0)

        state = await pipeline.run(agent, {"pdf_path": "test.pdf"})

        assert state.is_done
        assert state.retry_count == 1
        assert agent.run.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_input_includes_previous_attempt_error(self) -> None:
        """The pipeline must feed validation feedback back to the agent."""
        agent = _mock_agent([_bad_triage_missing_claim(), _good_triage()])
        pipeline = ValidationPipeline(validate_triage_report, "analyst", max_attempts=3, base_delay_s=0)

        await pipeline.run(agent, {"pdf_path": "test.pdf"})

        # The SECOND call should have received previous_attempt_error
        second_call_input = agent.run.call_args_list[1].args[0]
        assert "previous_attempt_error" in second_call_input
        pae = second_call_input["previous_attempt_error"]
        assert pae["attempt"] == 1
        assert isinstance(pae["violations"], list)
        assert len(pae["violations"]) > 0


# ── exhausted retries: escalate to MANUAL_REVIEW ─────────────────

class TestEscalationAfterMaxAttempts:
    @pytest.mark.asyncio
    async def test_always_bad_escalates_to_manual_review(self) -> None:
        agent = _mock_agent([_bad_triage_missing_claim()] * 5)  # always bad
        pipeline = ValidationPipeline(validate_triage_report, "analyst", max_attempts=3, base_delay_s=0)

        state = await pipeline.run(agent, {"pdf_path": "test.pdf"})

        assert state.is_done
        assert state.retry_count == 3
        assert agent.run.call_count == 3
        assert state.result is not None
        assert state.result.get("status") == "MANUAL_REVIEW"
        assert state.result.get("escalated") is True
        assert "Validation pipeline exhausted" in (state.last_error or "")

    @pytest.mark.asyncio
    async def test_auditor_exhausted_forces_manual_review_decision(self) -> None:
        agent = _mock_agent([_bad_decision_short_reasoning()] * 5, role="READ+WRITE")
        pipeline = ValidationPipeline(validate_claim_decision, "auditor", max_attempts=3, base_delay_s=0)

        state = await pipeline.run(
            agent,
            {"triage_report": _good_triage()},
            triage_report=_good_triage(),
        )

        assert state.result is not None
        assert state.result.get("decision") == "MANUAL_REVIEW"
        assert state.result.get("next_action") == "escalate_to_human"


# ── agent exception handling ─────────────────────────────────────

class TestAgentExceptionHandling:
    @pytest.mark.asyncio
    async def test_agent_exception_triggers_retry(self) -> None:
        """If the agent itself raises, that counts as a violation."""
        agent = _mock_agent([RuntimeError("LLM timeout"), _good_triage()])
        pipeline = ValidationPipeline(validate_triage_report, "analyst", max_attempts=3, base_delay_s=0)

        state = await pipeline.run(agent, {"pdf_path": "test.pdf"})

        assert state.is_done
        assert agent.run.call_count == 2

    @pytest.mark.asyncio
    async def test_repeated_exceptions_escalate(self) -> None:
        agent = _mock_agent([RuntimeError("always fails")] * 5)
        pipeline = ValidationPipeline(validate_triage_report, "analyst", max_attempts=3, base_delay_s=0)

        state = await pipeline.run(agent, {"pdf_path": "test.pdf"})

        assert state.result is not None
        assert state.result.get("status") == "MANUAL_REVIEW"
        assert state.result.get("escalated") is True


# ── boundary conditions ─────────────────────────────────────────

class TestBoundaryConditions:
    @pytest.mark.asyncio
    async def test_passes_on_last_allowed_attempt(self) -> None:
        agent = _mock_agent([
            _bad_triage_missing_claim(),
            _bad_triage_missing_claim(),
            _good_triage(),
        ])
        pipeline = ValidationPipeline(validate_triage_report, "analyst", max_attempts=3, base_delay_s=0)

        state = await pipeline.run(agent, {"pdf_path": "test.pdf"})

        assert state.is_done
        assert agent.run.call_count == 3
        assert state.result.get("status") == "EXTRACTED"
        assert state.result.get("escalated") is not True

    def test_invalid_max_attempts_raises(self) -> None:
        with pytest.raises(ValueError, match="max_attempts"):
            ValidationPipeline(validate_triage_report, "analyst", max_attempts=0, base_delay_s=0)
