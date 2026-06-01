"""User schema. The `sub` field is the JWT subject and the ACL anchor (Rule 7)."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from ..enums import UserRole


class User(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: UUID
    email: EmailStr
    role: UserRole
    full_name: str = Field(min_length=1, max_length=200)
    created_at: datetime
    updated_at: datetime
