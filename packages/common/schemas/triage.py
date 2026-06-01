"""TriageReport — analyst output.

Uses `extra="ignore"` to tolerate LLM verbosity without breaking (Rule 4).
`summary` is bounded at 200 chars and must be sanitized server-side before reaching UI.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..enums import SeverityLabel
from ..money import to_money


class TriageReport(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    pipeline_id: UUID
    policy_id: UUID
    pdf_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    reported_damage_amount: Decimal
    severity: SeverityLabel
    summary: str = Field(min_length=1, max_length=200)
    evidence_quotes: tuple[str, ...] = ()

    @field_validator("reported_damage_amount", mode="before")
    @classmethod
    def _coerce_money(cls, v: object) -> Decimal:
        return to_money(v)  # type: ignore[arg-type]
