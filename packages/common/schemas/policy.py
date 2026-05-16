"""Policy schema for the Insurance Automation System.

Rule 1: Shared schemas live exclusively in packages/common/.
Rule 11: All fields strictly typed via Pydantic.
Rule 2: Monetary fields use Decimal (never float) to avoid rounding errors.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, field_validator, model_validator

from packages.common.enums import PolicyStatus


class PolicySchema(BaseModel):
    """Represents an insurance contract."""

    policy_id: str
    user_id: int
    type: str
    status: PolicyStatus
    deductible_usd: Decimal
    coverage_limit_usd: Decimal
    start_date: date
    end_date: date

    # Audit timestamps (optional: not always present in Excel source)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator("type")
    @classmethod
    def type_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("type must not be empty")
        return v.strip()

    @field_validator("deductible_usd")
    @classmethod
    def deductible_must_be_non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("deductible_usd must be >= 0")
        return v

    @field_validator("coverage_limit_usd")
    @classmethod
    def coverage_limit_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("coverage_limit_usd must be > 0")
        return v

    @model_validator(mode="after")
    def business_rules(self) -> "PolicySchema":
        # Business rule: deductible < coverage limit
        if self.deductible_usd >= self.coverage_limit_usd:
            raise ValueError(
                f"deductible_usd ({self.deductible_usd}) must be less than "
                f"coverage_limit_usd ({self.coverage_limit_usd})"
            )
        # Business rule: end_date > start_date
        if self.end_date <= self.start_date:
            raise ValueError(
                f"end_date ({self.end_date}) must be after start_date ({self.start_date})"
            )
        return self


class PolicyCreateSchema(BaseModel):
    """Schema for inserting a new policy into the database."""

    policy_id: str
    user_id: int
    type: str
    status: PolicyStatus
    deductible_usd: Decimal
    coverage_limit_usd: Decimal
    start_date: date
    end_date: date
