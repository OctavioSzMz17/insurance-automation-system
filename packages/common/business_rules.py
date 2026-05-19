"""Cross-cutting business rules — pure functions, no I/O.

These live in `packages/common/` so every layer (orchestrator endpoints,
agent prompts, future batch jobs) can apply the same logic and stay in
agreement about what a piece of data *really* means right now.

Today this module hosts a single rule: the effective ("as of today")
status of a policy. Add more rules here as the system grows; keep them
all pure, testable, and free of side effects.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional


# Stored statuses that should auto-flip to EXPIRED once `end_date` is
# in the past. Anything else (CANCELLED, PENDING_PAYMENT, EXPIRED itself)
# stays as stored — those are operator decisions and we don't override.
_AUTO_EXPIRES_FROM = frozenset({"ACTIVE"})


def _coerce_date(value: date | str) -> date:
    """Accept either a `date` or an ISO-8601 date string."""
    if isinstance(value, date):
        return value
    # `date.fromisoformat` handles 'YYYY-MM-DD'; tolerate longer ISO strings.
    return date.fromisoformat(value[:10])


def compute_effective_policy_status(
    stored_status: str,
    end_date: date | str | None,
    today: Optional[date] = None,
) -> str:
    """Return the policy status as it should be *interpreted today*.

    If the stored status is `ACTIVE` but `end_date` is in the past, the
    effective status is `EXPIRED`. Every other stored status passes
    through untouched — we never override `CANCELLED`, `PENDING_PAYMENT`,
    etc. because those reflect deliberate operator actions.

    Args:
        stored_status: The `claim_status` value persisted in the DB.
        end_date:      The policy's `end_date` (date or ISO-8601 string).
                       `None` is allowed and short-circuits to the stored
                       value (we can't expire what has no end).
        today:         Override "current date" — useful in tests. Defaults
                       to today in UTC.

    Returns:
        The status the UI / agent should reason about.
    """
    if today is None:
        today = datetime.now(timezone.utc).date()

    if stored_status not in _AUTO_EXPIRES_FROM:
        return stored_status
    if end_date is None:
        return stored_status

    try:
        end = _coerce_date(end_date)
    except (TypeError, ValueError):
        # Malformed date — don't lie, return what we have.
        return stored_status

    return "EXPIRED" if end < today else stored_status
