"""ACL primitives shared across MCPs (Rule 1: single source of truth).

These are PURE functions — no DB access. The caller passes the relevant fields
already loaded, the helper enforces the policy and raises ForbiddenError on denial.

Why pure: keeps the policy tiny, auditable, and unit-testable without infra.
"""
from __future__ import annotations

from packages.common.enums import UserRole
from packages.common.exceptions import ForbiddenError


def _as_role(role: str | UserRole) -> UserRole:
    if isinstance(role, UserRole):
        return role
    try:
        # Case-insensitive: the orchestrator's auth layer emits lowercase role
        # values ('admin'), while UserRole is uppercase ('ADMIN'). Normalize so
        # the two layers agree instead of silently denying every call.
        return UserRole(role.upper())
    except (ValueError, AttributeError) as exc:
        raise ForbiddenError("forbidden") from exc


def is_admin(role: str | UserRole) -> bool:
    return _as_role(role) is UserRole.ADMIN


def assert_can_read_claim(*, caller_sub: str, caller_role: str | UserRole, submitted_by_sub: str) -> None:
    """ADMIN reads any claim; ANALYST only its own. Denial returns a single business-kind error.

    The error message is intentionally generic ('forbidden') — never echo the target sub
    to avoid leaking the existence of claims belonging to other users (Rule 7).
    """
    if is_admin(caller_role):
        return
    if not caller_sub or caller_sub != submitted_by_sub:
        raise ForbiddenError("forbidden")


def assert_admin_or_self_user(*, caller_sub: str, caller_role: str | UserRole, target_sub: str) -> None:
    """For users-MCP: ADMIN reads anyone, ANALYST reads only itself (anti-IDOR Rule 7)."""
    if is_admin(caller_role):
        return
    if not caller_sub or caller_sub != target_sub:
        raise ForbiddenError("forbidden")


def resolve_submitter_filter(*, caller_sub: str, caller_role: str | UserRole, requested_sub: str | None) -> str:
    """For list-style queries.

    ANALYST: the filter is ALWAYS forced to caller_sub. The `requested_sub` argument is ignored.
             This prevents an analyst from enumerating other submitters by guessing their sub.
    ADMIN:   respects `requested_sub`; if absent, defaults to listing every submitter.
    """
    if is_admin(caller_role):
        return requested_sub or "*"
    return caller_sub
