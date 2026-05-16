"""User schema for the Insurance Automation System.

Rule 1: Shared schemas live exclusively in packages/common/.
Rule 11: All fields strictly typed via Pydantic.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, field_validator


class UserSchema(BaseModel):
    """Represents an insured customer."""

    user_id: int
    name: str
    email: str

    # Audit timestamps (optional: not always present in Excel source)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator("name")
    @classmethod
    def name_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name must not be empty")
        return v.strip()

    @field_validator("email")
    @classmethod
    def email_must_be_valid(cls, v: str) -> str:
        if not v or "@" not in v:
            raise ValueError(f"'{v}' is not a valid email address")
        return v.strip().lower()

    @field_validator("user_id")
    @classmethod
    def user_id_must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("user_id must be a positive integer")
        return v


class UserCreateSchema(BaseModel):
    """Schema for inserting a new user into the database."""

    user_id: int
    name: str
    email: str
