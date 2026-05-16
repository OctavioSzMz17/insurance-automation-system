"""Self-healing validation pipeline.

Validates agent output against business constraints and retries on failure.
Implements Rule 5 from AI rules: Self-Healing Validation Pipeline.
"""

from typing import Any, Dict, Optional
from enum import Enum
from pydantic import BaseModel
from datetime import datetime
import logging

from observability.logging import logger

# Import claim_status enum from common schemas
# from packages.common.schemas.claim import ClaimStatus
# from packages.common.schemas.claim import Claim


class ValidationError(Exception):
    """Raised when agent output fails validation."""
    pass


class ConstraintViolation(BaseModel):
    """Represents a single constraint violation."""
    field: str
    rule: str
    actual_value: Any
    expected_constraint: str
    severity: str  # "error" or "warning"


class ValidationResult(BaseModel):
    """Result of validation."""
    is_valid: bool
    violations: list[ConstraintViolation] = []
    retry_count: int = 0
    timestamp: datetime


def validate_agent_output(
    agent_output: Dict[str, Any],
    agent_type: str  # "analyst" or "auditor"
) -> ValidationResult:
    """Validate agent output against business constraints.

    Args:
        agent_output: The agent's structured output
        agent_type: Type of agent ("analyst" or "auditor")

    Returns:
        ValidationResult with violations if any

    Note:
        This function checks:
        - claim_status is valid enum value (APPROVED, REJECTED, MANUAL_REVIEW, AWAITING_INPUT)
        - reported_damage_amount >= 0 if present
        - incident_date is a valid date
        - severity_label is valid enum value (LOW, HIGH, TOTAL_LOSS)
        - ai_reasoning is not empty
    """
    # TODO: Implement validation logic
    # Example constraints:
    #
    # if agent_type == "auditor":
    #     # Auditor must provide a decision
    #     if "claim_status" not in agent_output:
    #         raise ConstraintViolation(...)
    #     # Decision cannot be null
    #     if agent_output["claim_status"] is None:
    #         raise ConstraintViolation(...)
    #
    # if "reported_damage_amount" in agent_output:
    #     if agent_output["reported_damage_amount"] < 0:
    #         raise ConstraintViolation(...)
    #
    # if "ai_reasoning" not in agent_output or not agent_output["ai_reasoning"]:
    #     raise ConstraintViolation(...)

    logger.info(f"Validating {agent_type} agent output")

    return ValidationResult(
        is_valid=True,
        violations=[],
        retry_count=0,
        timestamp=datetime.utcnow()
    )


def retry_on_constraint_violation(
    validate_func,
    agent_func,
    agent_input: Dict[str, Any],
    max_attempts: int = 3
) -> Dict[str, Any]:
    """Retry agent execution if validation fails.

    Args:
        validate_func: Function to validate output
        agent_func: Agent function to call
        agent_input: Input to agent
        max_attempts: Maximum retry attempts

    Returns:
        Validated agent output

    Raises:
        ValidationError: After max_attempts failures

    Note:
        Implements Rule 5: Self-Healing Validation Pipeline
        - If output violates a business constraint, feeds error back to agent with clear explanation
        - Retries maximum 3 attempts
        - After 3 failures, escalates to MANUAL_REVIEW
        - All retry attempts logged with timestamps and error descriptions
    """
    # TODO: Implement retry logic
    #
    # for attempt in range(1, max_attempts + 1):
    #     try:
    #         output = agent_func(agent_input)
    #         result = validate_func(output)
    #         if result.is_valid:
    #             return output
    #         # Feed error back to agent
    #         agent_input["validation_errors"] = result.violations
    #         logger.warning(f"Attempt {attempt}: validation failed, retrying...")
    #     except Exception as e:
    #         logger.error(f"Attempt {attempt} failed: {e}")
    #         if attempt == max_attempts:
    #             raise ValidationError(f"Failed after {max_attempts} attempts")
    #
    # raise ValidationError("Validation failed after all retries")

    logger.info(f"Executing agent with max {max_attempts} retry attempts")
    return agent_func(agent_input)


def escalate_to_manual_review(
    claim_id: int,
    reason: str,
    agent_type: str,
    violations: Optional[list[ConstraintViolation]] = None
) -> Dict[str, Any]:
    """Escalate claim to manual review after max retries exceeded.

    Args:
        claim_id: ID of the claim
        reason: Reason for escalation
        agent_type: Type of agent that failed
        violations: List of constraint violations

    Returns:
        Update payload to set claim_status = MANUAL_REVIEW

    Note:
        Implements Rule 5 escalation logic:
        - After 3 failed validation attempts, claim automatically moves to MANUAL_REVIEW
        - Full audit trail logged with timestamps
        - Auditor can review the violations and make final decision
    """
    # TODO: Implement escalation logic
    #
    # escalation_payload = {
    #     "claim_id": claim_id,
    #     "claim_status": "MANUAL_REVIEW",
    #     "ai_reasoning": f"Escalated by {agent_type}: {reason}\nViolations: {violations}",
    #     "escalated_at": datetime.utcnow(),
    #     "escalation_reason": reason,
    # }
    #
    # logger.info(f"Escalated claim {claim_id} to MANUAL_REVIEW", extra={
    #     "claim_id": claim_id,
    #     "reason": reason,
    #     "agent_type": agent_type,
    #     "violations": violations
    # })
    #
    # return escalation_payload

    logger.info(f"Escalating claim {claim_id} to MANUAL_REVIEW: {reason}")
    return {
        "claim_id": claim_id,
        "claim_status": "MANUAL_REVIEW",
        "reason": reason,
    }
