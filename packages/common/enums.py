"""Domain enums shared across services. Single source of truth (ai-rules Rule 1)."""
from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    """System staff roles (the `users` table). Insured customers live in
    `clients` and have no role."""
    ANALYST = "ANALYST"
    ADMIN = "ADMIN"
    AUDITOR = "AUDITOR"


class PolicyStatus(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    SUSPENDED = "SUSPENDED"


class ClaimStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    AWAITING_INPUT = "AWAITING_INPUT"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


TERMINAL_CLAIM_STATUSES: frozenset[ClaimStatus] = frozenset(
    {ClaimStatus.APPROVED, ClaimStatus.REJECTED, ClaimStatus.MANUAL_REVIEW}
)


class SeverityLabel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ErrorKind(StrEnum):
    BUSINESS = "business"
    INTERNAL = "internal"
