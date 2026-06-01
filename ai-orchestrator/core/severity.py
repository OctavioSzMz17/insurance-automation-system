"""Deterministic severity classifier (Rule 4).

The LLM is FORBIDDEN from setting severity. This module is the single source of truth.
A malicious PDF cannot change the label because the function only consumes the validated
`reported_damage_amount` Decimal — the LLM's own opinion is discarded by the agent.

Thresholds (USD):
    [0,        1_000)     → LOW
    [1_000,   10_000)     → MEDIUM
    [10_000,  50_000)     → HIGH
    [50_000,    +∞)       → CRITICAL
"""
from __future__ import annotations

from decimal import Decimal
from typing import Final

from packages.common.enums import SeverityLabel
from packages.common.exceptions import ValidationFailure
from packages.common.money import to_money

_LOW_MAX: Final[Decimal] = Decimal("1000.00")
_MEDIUM_MAX: Final[Decimal] = Decimal("10000.00")
_HIGH_MAX: Final[Decimal] = Decimal("50000.00")


def compute(amount: Decimal | int | str) -> SeverityLabel:
    """Map a monetary amount to a SeverityLabel. Pure, total, deterministic."""
    money = to_money(amount)
    if money < _LOW_MAX:
        return SeverityLabel.LOW
    if money < _MEDIUM_MAX:
        return SeverityLabel.MEDIUM
    if money < _HIGH_MAX:
        return SeverityLabel.HIGH
    return SeverityLabel.CRITICAL


def assert_matches(amount: Decimal | int | str, claimed: SeverityLabel | str) -> None:
    """Validation helper used by the self-healing pipeline: raises if the claimed label
    does not match the deterministic computation."""
    expected = compute(amount)
    claimed_label = SeverityLabel(claimed) if not isinstance(claimed, SeverityLabel) else claimed
    if expected is not claimed_label:
        raise ValidationFailure(
            f"severity '{claimed_label.value}' does not match computed '{expected.value}' for amount {amount}"
        )
