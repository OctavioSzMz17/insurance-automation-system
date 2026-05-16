"""Shared enums for the Insurance Automation System.

These mirror the PostgreSQL ENUM types defined in the database schema.
Rule 1: All enums live exclusively in packages/common/ — never redefined elsewhere.
"""

from enum import Enum


class PolicyStatus(str, Enum):
    """Lifecycle state of an insurance policy."""
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    PENDING_PAYMENT = "PENDING_PAYMENT"
    CANCELLED = "CANCELLED"


class SeverityLabel(str, Enum):
    """AI-interpreted severity of a claim incident."""
    LOW = "LOW"
    HIGH = "HIGH"
    TOTAL_LOSS = "TOTAL_LOSS"


class ClaimStatus(str, Enum):
    """Processing state of a claim."""
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    AWAITING_INPUT = "AWAITING_INPUT"
