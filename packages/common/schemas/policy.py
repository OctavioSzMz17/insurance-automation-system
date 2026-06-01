"""Policy and coverage-rule schemas. Monetary fields are Decimal (Rule 2)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..enums import PolicyStatus
from ..money import to_money


class CoverageRule(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    code: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=500)
    included: bool
    sub_limit_usd: Decimal | None = None

    @field_validator("sub_limit_usd", mode="before")
    @classmethod
    def _coerce_limit(cls, v: object) -> object:
        return to_money(v) if v is not None else None  # type: ignore[arg-type]


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: UUID
    user_id: UUID
    status: PolicyStatus
    deductible_usd: Decimal
    coverage_limit_usd: Decimal
    effective_from: date
    effective_to: date
    coverage_rules: tuple[CoverageRule, ...] = ()
    created_at: datetime
    updated_at: datetime

    @field_validator("deductible_usd", "coverage_limit_usd", mode="before")
    @classmethod
    def _coerce_money(cls, v: object) -> Decimal:
        return to_money(v)  # type: ignore[arg-type]
