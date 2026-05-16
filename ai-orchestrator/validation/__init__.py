"""Self-healing validation pipeline (Rule 5).

Public API:
    ValidationPipeline       — wraps an agent run with validation + 3-attempt retry
    run_with_validation      — functional one-shot version
    ConstraintViolation      — single rule failure
    ValidationResult         — outcome of one validation pass
    ValidationError          — raised after all retries exhausted
    validate_triage_report   — validator for analyst output
    validate_claim_decision  — validator for auditor output
"""

from .pipeline import (
    ValidationPipeline,
    run_with_validation,
    ValidationError,
    ValidationResult,
    ConstraintViolation,
)
from .validators import validate_triage_report, validate_claim_decision

__all__ = [
    "ValidationPipeline",
    "run_with_validation",
    "ValidationError",
    "ValidationResult",
    "ConstraintViolation",
    "validate_triage_report",
    "validate_claim_decision",
]
