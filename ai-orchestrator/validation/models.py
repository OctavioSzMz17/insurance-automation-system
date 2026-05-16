"""Shared Pydantic models for the validation pipeline (B2.4).

Kept in their own module so both validators and the pipeline class can
import them without circular dependencies.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class ConstraintViolation(BaseModel):
    """One specific failure detected by a validator."""
    field:               str
    rule:                str
    actual_value:        Any
    expected_constraint: str
    severity:            Literal["error", "warning"] = "error"

    def human_message(self) -> str:
        """One-line description fed back to the agent on retry."""
        return (
            f"{self.field}: {self.rule} — got {self.actual_value!r}, "
            f"expected {self.expected_constraint}"
        )


class ValidationResult(BaseModel):
    """Outcome of one validation pass."""
    is_valid:    bool
    violations:  list[ConstraintViolation] = []
    attempt:     int
    timestamp:   datetime


class ValidationError(Exception):
    """Raised when all retry attempts have failed."""
    def __init__(self, message: str, violations: list[ConstraintViolation]):
        super().__init__(message)
        self.violations = violations
