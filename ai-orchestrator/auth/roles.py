"""Role definitions and hierarchy (Rule 7).

Roles are ordered — higher tier inherits everything lower tiers can do:

    analyst (1) → auditor (2) → admin (3)
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class Role(str, Enum):
    ANALYST = "analyst"
    AUDITOR = "auditor"
    ADMIN = "admin"


_HIERARCHY: dict[Role, int] = {
    Role.ANALYST: 1,
    Role.AUDITOR: 2,
    Role.ADMIN: 3,
}


def has_role_or_higher(actual: Role, required: Role) -> bool:
    return _HIERARCHY[actual] >= _HIERARCHY[required]


class UserPrincipal(BaseModel):
    sub: str
    role: Role
