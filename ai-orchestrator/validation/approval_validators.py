"""Input validators for human-approval endpoints — C2.2.

Wraps POST /claims/{id}/approve and /reject with four guardrails:

    1. reason_too_short          — "ok" / "yes" are rejected outright (422)
    2. reason_has_control_chars  — basic sanity check on input chars   (422)
    3. idempotency_replay        — same actor+claim+action within 5min
                                   returns the cached result, never
                                   double-writes                       (200, cache hit)
    4. actor_rate_limited        — > 20 actions / 60s per actor        (429)

This is NOT a retry loop — these are user-input checks. Failures short-
circuit immediately. The existing ValidationPipeline (B3.1) is for
agent retries, not human input.

The two caches (idempotency, rate window) are in-memory dicts. For a
single-replica orchestrator this is fine. For multi-replica deploys
they would move to Redis; the public functions stay the same.

Rules applied:
    Rule 4  — audit-friendly reason quality
    Rule 6  — strengthens the human-confirmation step
    Rule 9  — structured logging on every block
    Rule 11 — type hints throughout
"""

from __future__ import annotations

import logging
import re
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from .models import ConstraintViolation

logger = logging.getLogger(__name__)


# ── Tuning ────────────────────────────────────────────────────────

MIN_REASON_LEN     = 20            # chars (after .strip())
IDEMPOTENCY_TTL    = timedelta(minutes=5)
RATE_LIMIT_WINDOW  = timedelta(seconds=60)
RATE_LIMIT_MAX     = 20            # actions per actor per window

# Control characters we never want in stored audit text.
# We allow newline (\n=0x0A), tab (\t=0x09), CR (\x0D); reject everything else.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


Action = Literal["approve", "reject"]


# ── Caches (module-level singletons, thread-safe) ────────────────

@dataclass
class _IdempotencyEntry:
    timestamp: datetime
    result:    dict[str, Any]


# (actor, claim_id, action) → cached result
_idempotency_cache: dict[tuple[str, int, str], _IdempotencyEntry] = {}

# actor → deque of timestamps within the last RATE_LIMIT_WINDOW
_rate_windows: dict[str, deque[datetime]] = {}

_lock = threading.Lock()


# ── Public API ────────────────────────────────────────────────────

@dataclass(frozen=True)
class ApprovalCheckResult:
    """Outcome of validate_approval_input.

    Exactly one of these is meaningful:
        - violations non-empty → reject with 422 / 429 (mapped from rule)
        - cached_result not None → idempotency hit; return as 200
        - both empty → caller proceeds to the MCP write
    """
    violations:        list[ConstraintViolation]   = field(default_factory=list)
    cached_result:     dict[str, Any] | None       = None
    retry_after_secs:  int | None                  = None


def validate_approval_input(
    actor:     str,
    claim_id:  int,
    action:    Action,
    reason:    str,
) -> ApprovalCheckResult:
    """Run all 4 input checks. Cheap (microseconds).

    Order matters — we evaluate cheapest first and short-circuit:
        1. format (reason length, control chars)
        2. rate limit
        3. idempotency replay

    Args:
        actor:    The authenticated actor (JWT `sub`).
        claim_id: Target claim primary key.
        action:   "approve" or "reject".
        reason:   The text the user supplied in the request body.

    Returns:
        ApprovalCheckResult — see field docs.
    """
    # 1 + 2: format checks
    fmt_violations = _check_reason(reason)
    if fmt_violations:
        return ApprovalCheckResult(violations=fmt_violations)

    # 3: rate limit
    rate_violations, retry_after = _check_rate_limit(actor)
    if rate_violations:
        return ApprovalCheckResult(
            violations=rate_violations,
            retry_after_secs=retry_after,
        )

    # 4: idempotency replay
    cached = _check_idempotency(actor, claim_id, action)
    if cached is not None:
        logger.info(
            f"approval idempotency HIT | actor={actor} claim_id={claim_id} "
            f"action={action} returning cached result"
        )
        return ApprovalCheckResult(cached_result=cached)

    return ApprovalCheckResult()


def record_approval_action(
    actor:    str,
    claim_id: int,
    action:   Action,
    result:   dict[str, Any],
) -> None:
    """Save the result + tick the rate counter after a SUCCESSFUL write.

    Must be called only when the underlying MCP write succeeded;
    failed writes should be retryable.
    """
    now = datetime.now(timezone.utc)
    key = (actor, claim_id, action)
    with _lock:
        _idempotency_cache[key] = _IdempotencyEntry(timestamp=now, result=result)
        _rate_windows.setdefault(actor, deque()).append(now)
    _evict_expired(now)
    logger.debug(
        f"approval recorded | actor={actor} claim_id={claim_id} action={action}"
    )


def reset_caches() -> None:
    """Wipe both caches. Test-only helper."""
    with _lock:
        _idempotency_cache.clear()
        _rate_windows.clear()


# ── Internals ─────────────────────────────────────────────────────

def _check_reason(reason: str) -> list[ConstraintViolation]:
    violations: list[ConstraintViolation] = []
    stripped = (reason or "").strip()

    if len(stripped) < MIN_REASON_LEN:
        violations.append(ConstraintViolation(
            field="reason",
            rule="reason_too_short",
            actual_value=f"len={len(stripped)}",
            expected_constraint=(
                f"at least {MIN_REASON_LEN} characters explaining the "
                f"decision (Rule 4 — audit-grade reasoning)"
            ),
            severity="error",
        ))

    if _CONTROL_RE.search(reason or ""):
        violations.append(ConstraintViolation(
            field="reason",
            rule="reason_has_control_chars",
            actual_value="contains non-printable characters",
            expected_constraint="only printable text + standard whitespace",
            severity="error",
        ))

    return violations


def _check_rate_limit(actor: str) -> tuple[list[ConstraintViolation], int | None]:
    now = datetime.now(timezone.utc)
    cutoff = now - RATE_LIMIT_WINDOW

    with _lock:
        dq = _rate_windows.get(actor)
        if dq is None:
            return [], None
        # Trim expired entries from the left
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= RATE_LIMIT_MAX:
            # Time until the oldest entry falls out of the window
            retry_after = max(1, int((dq[0] + RATE_LIMIT_WINDOW - now).total_seconds()))
            logger.warning(
                f"approval rate-limit BLOCK | actor={actor} count={len(dq)} "
                f"limit={RATE_LIMIT_MAX} window={RATE_LIMIT_WINDOW.total_seconds()}s "
                f"retry_after={retry_after}s"
            )
            return [ConstraintViolation(
                field="actor",
                rule="actor_rate_limited",
                actual_value=f"{len(dq)} actions in last {RATE_LIMIT_WINDOW.total_seconds():.0f}s",
                expected_constraint=(
                    f"at most {RATE_LIMIT_MAX} actions per "
                    f"{RATE_LIMIT_WINDOW.total_seconds():.0f}s"
                ),
                severity="error",
            )], retry_after

    return [], None


def _check_idempotency(
    actor: str, claim_id: int, action: str,
) -> dict[str, Any] | None:
    key = (actor, claim_id, action)
    now = datetime.now(timezone.utc)
    with _lock:
        entry = _idempotency_cache.get(key)
        if entry is None:
            return None
        if now - entry.timestamp > IDEMPOTENCY_TTL:
            _idempotency_cache.pop(key, None)
            return None
        return entry.result


def _evict_expired(now: datetime) -> None:
    """Opportunistic cleanup of stale entries — O(N) but N is small."""
    cutoff_idemp = now - IDEMPOTENCY_TTL
    cutoff_rate  = now - RATE_LIMIT_WINDOW
    with _lock:
        expired_idemp = [
            k for k, v in _idempotency_cache.items() if v.timestamp < cutoff_idemp
        ]
        for k in expired_idemp:
            _idempotency_cache.pop(k, None)

        empty_actors: list[str] = []
        for actor, dq in _rate_windows.items():
            while dq and dq[0] < cutoff_rate:
                dq.popleft()
            if not dq:
                empty_actors.append(actor)
        for a in empty_actors:
            _rate_windows.pop(a, None)


# ── HTTP status mapping helper ───────────────────────────────────

def http_status_for(violations: list[ConstraintViolation]) -> int:
    """Map a list of approval violations to the right HTTP status code."""
    codes = {v.rule for v in violations}
    if "actor_rate_limited" in codes:
        return 429  # Too Many Requests
    return 422      # Unprocessable Entity for everything else
