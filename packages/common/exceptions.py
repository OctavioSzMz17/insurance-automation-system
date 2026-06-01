"""Domain exceptions. Always import the class, never match by string name (Rule 1)."""
from __future__ import annotations


class DomainError(Exception):
    """Base for all expected domain errors. Surfaces as kind=business."""


class BusinessError(DomainError):
    """Generic business-rule violation surfaced safely to the caller."""


class ClaimStateConflictError(DomainError):
    """Attempted state transition violates the claim lifecycle (e.g., MANUAL_REVIEW → AWAITING_INPUT)."""


class ForbiddenError(DomainError):
    """Row-level ACL denial. Surfaces as a business-kind error so internals are not leaked."""


class NotFoundError(DomainError):
    """Resource does not exist or caller is not permitted to know that it does."""


class ValidationFailure(DomainError):
    """Input failed semantic validation beyond Pydantic schema checks."""


class MCPClientError(Exception):
    """Raised by orchestrator-side MCP client. `public` is safe to return to HTTP responses."""

    def __init__(self, public: str, *, internal: str | None = None) -> None:
        super().__init__(public)
        self.public = public
        self.internal = internal or public
