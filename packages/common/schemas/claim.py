"""Claim, ClaimDecision, ClaimProposal schemas.

- Claim row: includes `submitted_by_sub` (ACL anchor) and `decision_metadata` (audit) — Rule 2.
- ClaimDecision: strict=forbid because it is the auditor security boundary — Rule 4.
- ClaimProposal: HMAC-signed handoff between agent and human-approval gate — Rule 6.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..enums import ClaimStatus, SeverityLabel
from ..money import to_money


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: UUID
    submitted_by_sub: str = Field(min_length=1, max_length=255)
    policy_id: UUID
    pdf_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    reported_damage_amount: Decimal
    severity: SeverityLabel
    status: ClaimStatus
    decision_metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    @field_validator("reported_damage_amount", mode="before")
    @classmethod
    def _coerce_money(cls, v: object) -> Decimal:
        return to_money(v)  # type: ignore[arg-type]


class ClaimSubmitRequest(BaseModel):
    """Request body for claim submission. `pdf_path` is validated by the orchestrator (Rule 13)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    policy_id: UUID
    pdf_path: str = Field(min_length=1, max_length=512)


class ClaimDecision(BaseModel):
    """Auditor output. Strict forbid — this is the security boundary (Rule 4)."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    claim_id: UUID
    decision: Literal["APPROVED", "REJECTED", "MANUAL_REVIEW"]
    reason: str = Field(min_length=1, max_length=2000)
    evidence: tuple[str, ...] = ()
    model: str = Field(min_length=1, max_length=128)
    model_version: str = Field(min_length=1, max_length=64)
    decided_at: datetime


class ClaimProposal(BaseModel):
    """Signed handoff object emitted by an agent for human approval (Rule 6)."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    proposal_id: UUID
    pipeline_id: UUID
    actor: Literal["analyst", "auditor"]
    payload_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    signature_hex: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    issued_at: datetime
    expires_at: datetime
