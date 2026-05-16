"""Output validators for agent responses.

Each validator function takes an agent output dict (and optional context)
and returns a list of ConstraintViolation. Empty list = passed.

Public entry points:
    validate_triage_report(output) -> list[ConstraintViolation]
    validate_claim_decision(output, triage_report) -> list[ConstraintViolation]
"""

from .triage_validators import validate_triage_report
from .decision_validators import validate_claim_decision

__all__ = ["validate_triage_report", "validate_claim_decision"]
