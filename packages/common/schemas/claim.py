"""Claim schema for the Insurance Automation System.

Rule 1: Shared schemas live exclusively in packages/common/.
Rule 11: All fields strictly typed via Pydantic.
Rule 2: Monetary fields use Decimal (never float).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from packages.common.enums import ClaimStatus, SeverityLabel


class ClaimSchema(BaseModel):
    """Full claim record as stored in the database."""

    claim_id:               Optional[int] = None          # set by BIGSERIAL on insert
    user_id:                int
    policy_id:              int
    incident_date:          date
    incident_description:   str
    incident_location:      Optional[str] = None
    severity_label:         SeverityLabel
    reported_damage_amount: Optional[Decimal] = None
    claim_status:           ClaimStatus
    ai_reasoning:           str

    # Audit timestamps — set by DB
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator("incident_description")
    @classmethod
    def description_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("incident_description must not be empty")
        return v.strip()

    @field_validator("ai_reasoning")
    @classmethod
    def reasoning_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("ai_reasoning must not be empty (Rule 4)")
        return v.strip()

    @field_validator("reported_damage_amount")
    @classmethod
    def amount_must_be_non_negative(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v < 0:
            raise ValueError("reported_damage_amount must be >= 0")
        return v


class ClaimCreateSchema(BaseModel):
    """Input schema for inserting a new claim — what the auditor_agent submits."""

    user_id:                int
    policy_id:              int
    incident_date:          date
    incident_description:   str = Field(..., min_length=1)
    incident_location:      Optional[str] = None
    severity_label:         SeverityLabel
    reported_damage_amount: Optional[Decimal] = None
    claim_status:           ClaimStatus
    ai_reasoning:           str = Field(..., min_length=1)
