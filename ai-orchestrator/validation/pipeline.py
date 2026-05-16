"""Self-healing validation pipeline (Rule 5) — B2.4.

Wraps an agent run with output validation + retry logic:

    1. Call agent.run(input)
    2. Validate output against the configured validators
    3. If valid           → return state (success)
    4. If invalid AND attempt < max_attempts:
            attach violations to input as `previous_attempt_error`
            increment retry_count, loop back to step 1
    5. If max_attempts exhausted:
            force decision/status to MANUAL_REVIEW
            return state with last_error set

Every attempt + violation is logged.

Rules applied:
    Rule 4  — Reasoning trail preserved; agents get a fair retry with feedback
    Rule 5  — 3-attempt cap, MANUAL_REVIEW escalation
    Rule 9  — Structured logging on every attempt
    Rule 11 — Type hints throughout
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol

from .models import ConstraintViolation, ValidationError, ValidationResult

logger = logging.getLogger(__name__)


# Validator signature: (agent_output, context) → list of violations
ValidatorFn = Callable[..., list[ConstraintViolation]]


class _RunnableAgent(Protocol):
    """Duck-typed protocol for anything ValidationPipeline can wrap."""
    role: str
    async def run(self, agent_input: dict[str, Any]) -> Any: ...  # returns AgentState


class ValidationPipeline:
    """Wraps an agent run with validation + retry.

    Attributes:
        validator:     Callable producing ConstraintViolation list.
                       Signature: (agent_output, **context) -> list[ConstraintViolation].
        max_attempts:  Total tries allowed (default 3 per Rule 5).
        agent_kind:    "analyst" or "auditor" — only used for logging + escalation.
    """

    def __init__(
        self,
        validator:    ValidatorFn,
        agent_kind:   str,
        max_attempts: int = 3,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.validator    = validator
        self.agent_kind   = agent_kind
        self.max_attempts = max_attempts

    # ── public entry point ────────────────────────────────────────

    async def run(
        self,
        agent:        _RunnableAgent,
        agent_input:  dict[str, Any],
        **validator_context: Any,
    ) -> Any:
        """Run `agent` with validation + retry. Returns the agent's AgentState.

        Args:
            agent:               Any object with async .run(input) → AgentState.
            agent_input:         The input passed to agent.run on attempt 1.
            **validator_context: Extra kwargs passed to the validator on every call
                                 (e.g. triage_report=... for the decision validator).
        """
        last_violations: list[ConstraintViolation] = []
        state: Any = None

        # Make a shallow copy so we can mutate without affecting the caller
        current_input = dict(agent_input)

        for attempt in range(1, self.max_attempts + 1):
            logger.info(
                f"ValidationPipeline | agent={self.agent_kind} attempt={attempt}/{self.max_attempts}"
            )

            # 1. Run the agent
            try:
                state = await agent.run(current_input)
            except Exception as e:
                last_violations = [ConstraintViolation(
                    field="<agent>",
                    rule="agent_exception",
                    actual_value=str(e)[:200],
                    expected_constraint="agent.run must return cleanly",
                )]
                logger.error(f"Agent raised on attempt {attempt}: {e}")
                self._prepare_retry_input(current_input, last_violations, attempt)
                continue

            # 2. Did the agent itself fail (state.last_error set, no result)?
            if state.last_error or state.result is None:
                last_violations = [ConstraintViolation(
                    field="<agent>",
                    rule="agent_returned_no_result",
                    actual_value=state.last_error or "result is None",
                    expected_constraint="non-null structured result",
                )]
                logger.warning(
                    f"Agent had no usable result on attempt {attempt}: {state.last_error}"
                )
                self._prepare_retry_input(current_input, last_violations, attempt)
                continue

            # 3. Validate the agent's output
            violations = self.validator(state.result, **validator_context)
            result_obj = ValidationResult(
                is_valid=not violations,
                violations=violations,
                attempt=attempt,
                timestamp=datetime.now(timezone.utc),
            )
            state.log_step(
                "validation",
                is_valid=result_obj.is_valid,
                violations=[v.model_dump() for v in violations],
                attempt=attempt,
            )

            if result_obj.is_valid:
                logger.info(
                    f"ValidationPipeline | agent={self.agent_kind} attempt={attempt} PASSED"
                )
                state.retry_count = attempt - 1
                return state

            # 4. Failed — record + prepare for next attempt
            last_violations = violations
            logger.warning(
                f"ValidationPipeline | agent={self.agent_kind} attempt={attempt} "
                f"FAILED with {len(violations)} violation(s): "
                f"{[v.rule for v in violations]}"
            )
            self._prepare_retry_input(current_input, violations, attempt)
            state.retry_count = attempt

        # 5. All attempts exhausted — escalate to MANUAL_REVIEW (Rule 5)
        logger.error(
            f"ValidationPipeline | agent={self.agent_kind} EXHAUSTED "
            f"{self.max_attempts} attempts — escalating to MANUAL_REVIEW"
        )
        return self._escalate(state, last_violations)

    # ── helpers ───────────────────────────────────────────────────

    @staticmethod
    def _prepare_retry_input(
        agent_input:  dict[str, Any],
        violations:   list[ConstraintViolation],
        attempt:      int,
    ) -> None:
        """Inject feedback so the next agent call can self-correct."""
        agent_input["previous_attempt_error"] = {
            "attempt":     attempt,
            "violations":  [v.human_message() for v in violations],
        }

    def _escalate(
        self,
        state:       Any,
        violations:  list[ConstraintViolation],
    ) -> Any:
        """Force MANUAL_REVIEW into the agent state and surface the reason."""
        reason = (
            f"Validation pipeline exhausted {self.max_attempts} attempts. "
            f"Final violations: {[v.human_message() for v in violations]}"
        )
        escalation: dict[str, Any] = {
            "decision":     "MANUAL_REVIEW",
            "status":       "MANUAL_REVIEW",
            "ai_reasoning": reason,
            "next_action":  "escalate_to_human",
            "escalated":    True,
            "violations":   [v.model_dump() for v in violations],
        }

        # If we have something usable from the agent, merge with escalation
        if state is not None and state.result:
            merged = dict(state.result)
            merged.update(escalation)
            state.result = merged
        else:
            # Create a minimal state shell if the agent never produced one
            from agents.reasoning import AgentState  # noqa: WPS433 — lazy import
            if state is None:
                state = AgentState(claim_input={})
            state.result = escalation

        state.last_error = reason
        state.retry_count = self.max_attempts
        state.is_done = True
        if hasattr(state, "log_step"):
            state.log_step(
                "escalated",
                reason=reason,
                violations=[v.model_dump() for v in violations],
            )
        return state


# ── module-level convenience wrappers (used by orchestrator wiring) ─

async def run_with_validation(
    agent:               _RunnableAgent,
    agent_input:         dict[str, Any],
    validator:           ValidatorFn,
    agent_kind:          str,
    max_attempts:        int = 3,
    **validator_context: Any,
) -> Any:
    """Functional shortcut around ValidationPipeline for one-off use."""
    pipeline = ValidationPipeline(validator, agent_kind, max_attempts)
    return await pipeline.run(agent, agent_input, **validator_context)


__all__ = [
    "ValidationPipeline",
    "ValidationError",
    "ValidationResult",
    "ConstraintViolation",
    "run_with_validation",
]
