"""Truth table for the deterministic severity classifier."""
from __future__ import annotations

from decimal import Decimal

import pytest

from core import severity
from packages.common.enums import SeverityLabel
from packages.common.exceptions import ValidationFailure


@pytest.mark.parametrize(
    "amount,expected",
    [
        ("0.00",      SeverityLabel.LOW),
        ("1.00",      SeverityLabel.LOW),
        ("999.99",    SeverityLabel.LOW),
        ("1000.00",   SeverityLabel.MEDIUM),
        ("5000.00",   SeverityLabel.MEDIUM),
        ("9999.99",   SeverityLabel.MEDIUM),
        ("10000.00",  SeverityLabel.HIGH),
        ("25000.00",  SeverityLabel.HIGH),
        ("49999.99",  SeverityLabel.HIGH),
        ("50000.00",  SeverityLabel.CRITICAL),
        ("250000.00", SeverityLabel.CRITICAL),
        (Decimal("999999999.99"), SeverityLabel.CRITICAL),
    ],
)
def test_severity_truth_table(amount, expected):
    assert severity.compute(amount) is expected


def test_float_rejected_at_to_money_layer():
    with pytest.raises(ValidationFailure):
        severity.compute(1500.5)  # float not allowed (Rule 2)


def test_assert_matches_ok():
    severity.assert_matches("100.00", SeverityLabel.LOW)


def test_assert_matches_raises_on_mismatch():
    with pytest.raises(ValidationFailure):
        severity.assert_matches("100.00", SeverityLabel.CRITICAL)


def test_assert_matches_accepts_string_label():
    severity.assert_matches("100.00", "LOW")
