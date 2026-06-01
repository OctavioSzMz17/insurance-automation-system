"""Money helpers. All monetary values are Decimal with 2 decimal places (Rule 2: NUMERIC, never FLOAT)."""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Final

from .exceptions import ValidationFailure

MONEY_QUANTUM: Final[Decimal] = Decimal("0.01")
MAX_MONEY: Final[Decimal] = Decimal("99999999999.99")
MIN_MONEY: Final[Decimal] = Decimal("0.00")


def to_money(value: Decimal | int | str) -> Decimal:
    """Coerce a value into a quantized non-negative Decimal usable for NUMERIC(14,2) columns.

    Floats are rejected on purpose: silent binary-rounding error is the bug class
    Rule 2 exists to prevent.
    """
    if isinstance(value, float):
        raise ValidationFailure("monetary value must not be a float")
    try:
        d = Decimal(value) if not isinstance(value, Decimal) else value
    except (InvalidOperation, TypeError) as exc:
        raise ValidationFailure("monetary value is not a valid decimal") from exc
    if d.is_nan() or d.is_infinite():
        raise ValidationFailure("monetary value must be finite")
    q = d.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)
    if q < MIN_MONEY or q > MAX_MONEY:
        raise ValidationFailure("monetary value out of allowed range")
    return q
