"""Service principals used by the orchestrator's own background work (Rule 7).

Some MCP calls are not made *for* a user — they are made *by the system*, about a user.
The deterministic verification in `core/verification.py` is the clearest case: when the
orchestrator re-reads a policy to decide whether coverage is in force, that is the system
establishing a fact, not an operator querying a record. Borrowing the operator's identity
for it conflates the two, and the anti-enumeration ACL on the users MCP then refuses the
call — which, combined with a best-effort `except`, silently disables the check.

So these calls carry an explicit system principal instead. The `sub` is namespaced with
`system:` so it is distinguishable in every audit log and can never collide with a real
user, whose `sub` is always an email address.

Why ADMIN: the MCP ACL has two tiers, and read-anything is the lower-privilege half of
ADMIN. Writes are gated separately by `@write_restricted` + `MCP_WRITE_ENABLED`, so a
read-only service principal at ADMIN level cannot mutate anything. A dedicated
`SERVICE` role with read-only grants on the MCP side would be tighter and is the right
follow-up; it is a schema and ACL change rather than a wiring change.

These principals are reachable only from orchestrator code. No JWT can mint them: the
`sub` is not an email, and `POST /auth/token` is disabled outside development.
"""
from __future__ import annotations

from typing import Final

#: Re-reads policies, clients and documents to compute the deterministic checks.
VERIFICATION_SUB: Final[str] = "system:verification"

#: Creates MANUAL_REVIEW claims for HITL proposals that expired unanswered.
HITL_REAPER_SUB: Final[str] = "system:hitl_reaper"

#: Read-anything, write-never. See the module docstring.
SERVICE_ROLE: Final[str] = "ADMIN"


def verification_identity() -> dict[str, str]:
    """Caller fields for a deterministic-verification MCP call."""
    return {"caller_sub": VERIFICATION_SUB, "caller_role": SERVICE_ROLE}


def reaper_identity() -> dict[str, str]:
    """Caller fields for the HITL timeout reaper."""
    return {"caller_sub": HITL_REAPER_SUB, "caller_role": SERVICE_ROLE}


__all__ = [
    "HITL_REAPER_SUB",
    "SERVICE_ROLE",
    "VERIFICATION_SUB",
    "reaper_identity",
    "verification_identity",
]
