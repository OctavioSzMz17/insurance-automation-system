"""Document schema. `sha256` is required for dedup (Rule 13)."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: UUID
    claim_id: UUID | None
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    mime: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(ge=1, le=50 * 1024 * 1024)
    storage_path: str = Field(min_length=1, max_length=512)
    created_at: datetime
