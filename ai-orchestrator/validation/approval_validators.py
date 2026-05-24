"""Input validators for human-approval endpoints — C2.2 + Fix D.

Fix D changes:
    1. Idempotency key now includes sha256(reason)[:16] so two calls with
       the same actor+claim+action but different reasons produce two
       independent DB writes — closes the audit-trail tampering vector.
    2. When a Redis client is configured (via configure_redis()), both the
       idempotency cache and the rate-limit window use Redis so all
       orchestrator replicas share the same state.
    3. record_approval_action now accepts a `reason` parameter (required
       to build the full key).

Wraps POST /claims/{id}/approve, /reject and /confirm with four guardrails:

    1. reason_too_short          — "ok" / "yes" are rejected outright (422)
    2. reason_has_control_chars  — basic sanity check on input chars   (422)
    3. idempotency_replay        — same actor+claim+action+reason within
                                   5 min returns the cached result, never
                                   double-writes                       (200)
    4. actor_rate_limited        — > 20 actions / 60s per actor        (429)

Rules applied:
    Rule 4  — audit-friendly reason quality
    Rule 6  — strengthens the human-confirmation step
    Rule 8  — Redis URL comes from Settings / .env via configure_redis()
    Rule 9  — structured logging on every block
    Rule 11 — type hints throughout
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from .models import ConstraintViolation

logger = logging.getLogger(__name__)


# ── Tuning ────────────────────────────────────────────────────────

MIN_REASON_LEN     = 20            # chars (after .strip())
IDEMPOTENCY_TTL    = timedelta(minutes=5)
RATE_LIMIT_WINDOW  = timedelta(seconds=60)
RATE_LIMIT_MAX     = 20            # actions per actor per window

# Control characters we never want in stored audit text.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


Action = Literal["approve", "reject", "confirm"]


# ── Redis client (optional, injected at startup) ──────────────────

_redis_client = None   # redis.asyncio.Redis | None
_redis_lock   = threading.Lock()


def configure_redis(client) -> None:
    """Inject the shared Redis client at app startup.

    Called by app.py lifespan after create_redis_client() returns a
    connected client. Safe to call with None (falls back to in-memory).
    """
    global _redis_client
    with _redis_lock:
        _redis_client = client
    backend = "redis" if client is not None else "in-memory"
    logger.info("ApprovalValidators backend | %s", backend)


# ── In-memory fallback caches (single-replica / dev / test) ──────

@dataclass
class _IdempotencyEntry:
    timestamp: datetime
    result:    dict[str, Any]


# (actor, claim_id, action, reason_hash) → cached result
_idempotency_cache: dict[tuple[str, int, str, str], _IdempotencyEntry] = {}

# actor → deque of timestamps within the last RATE_LIMIT_WINDOW
_rate_windows: dict[str, deque[datetime]] = {}

_lock = threading.Lock()


# ── Key helpers ───────────────────────────────────────────────────

def _hash_reason(reason: str) -> str:
    """Return a 16-char hex digest of reason (sha256 truncated).

    Not a secret — used only to distinguish different reason strings
    within the idempotency key. Truncation to 16 chars keeps Redis keys
    compact while keeping collision probability negligible (~2^-64).
    """
    return hashlib.sha256(reason.encode("utf-8", errors="replace")).hexdigest()[:16]


def _idemp_key(actor: str, claim_id: int, action: str, reason_hash: str) -> str:
    """Redis key for one idempotency entry."""
    return f"idemp:{actor}:{claim_id}:{action}:{reason_hash}"


def _rate_key(actor: str) -> str:
    """Redis sorted-set key for an actor's rate-limit window."""
    return f"rate:{actor}"


# ── Public API ────────────────────────────────────────────────────

@dataclass(frozen=True)
class ApprovalCheckResult:
    """Outcome of validate_approval_input.

    Exactly one of these is meaningful:
        - violations non-empty → reject with 422 / 429
        - cached_result not None → idempotency hit; return as 200
        - both empty → caller proceeds to the MCP write
    """
    violations:        list[ConstraintViolation]   = field(default_factory=list)
    cached_result:     dict[str, Any] | None       = None
    retry_after_secs:  int | None                  = None


async def validate_approval_input(
    actor:     str,
    claim_id:  int,
    action:    Action,
    reason:    str,
) -> ApprovalCheckResult:
    """Run all 4 input checks. Returns ApprovalCheckResult.

    Fix D: now async (Redis idempotency/rate checks are async).
    Order: format → rate limit → idempotency replay.

    Args:
        actor:    The authenticated actor (JWT sub).
        claim_id: Target claim primary key.
        action:   "approve", "reject", or "confirm".
        reason:   The text the user supplied in the request body.
    """
    # 1+2: format checks (synchronous, cheap)
    fmt_violations = _check_reason(reason)
    if fmt_violations:
        return ApprovalCheckResult(violations=fmt_violations)

    reason_hash = _hash_reason(reason)

    # 3: rate limit
    rate_violations, retry_after = await _check_rate_limit(actor)
    if rate_violations:
        return ApprovalCheckResult(
            violations=rate_violations,
            retry_after_secs=retry_after,
        )

    # 4: idempotency replay
    cached = await _check_idempotency(actor, claim_id, action, reason_hash)
    if cached is not None:
        logger.info(
            "approval idempotency HIT | actor=%s claim_id=%s action=%s",
            actor, claim_id, action,
        )
        return ApprovalCheckResult(cached_result=cached)

    return ApprovalCheckResult()


async def record_approval_action(
    actor:    str,
    claim_id: int,
    action:   Action,
    result:   dict[str, Any],
    reason:   str,
) -> None:
    """Save the result + tick the rate counter after a SUCCESSFUL write.

    Fix D: accepts `reason` so the full key (including reason hash) is
    recorded. Must be called only when the underlying MCP write succeeded.

    Args:
        actor:    JWT sub of the approving user.
        claim_id: Claim primary key.
        action:   "approve", "reject", or "confirm".
        result:   The persisted claim row dict from the MCP write.
        reason:   The reason text supplied by the user.
    """
    reason_hash = _hash_reason(reason)
    now = datetime.now(timezone.utc)

    if _redis_client is not None:
        await _record_redis(actor, claim_id, action, reason_hash, result, now)
    else:
        _record_local(actor, claim_id, action, reason_hash, result, now)
        _evict_expired(now)

    logger.debug(
        "approval recorded | actor=%s claim_id=%s action=%s", actor, claim_id, action,
    )


def reset_caches() -> None:
    """Wipe both in-memory caches. Test-only helper."""
    with _lock:
        _idempotency_cache.clear()
        _rate_windows.clear()


# ── Redis backend ─────────────────────────────────────────────────

async def _check_idempotency_redis(
    actor: str, claim_id: int, action: str, reason_hash: str,
) -> dict[str, Any] | None:
    key = _idemp_key(actor, claim_id, action, reason_hash)
    raw = await _redis_client.get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    except (json.JSONDecodeError, AttributeError):
        return None


async def _check_rate_limit_redis(
    actor: str,
) -> tuple[list[ConstraintViolation], int | None]:
    key     = _rate_key(actor)
    now_ts  = time.time()
    cutoff  = now_ts - RATE_LIMIT_WINDOW.total_seconds()

    pipe = _redis_client.pipeline()
    # Remove entries older than the window
    pipe.zremrangebyscore(key, "-inf", cutoff)
    # Count remaining entries in the window
    pipe.zcard(key)
    _, count = await pipe.execute()

    if count >= RATE_LIMIT_MAX:
        # Oldest entry in the window
        oldest = await _redis_client.zrange(key, 0, 0, withscores=True)
        if oldest:
            oldest_ts = oldest[0][1]
            retry_after = max(1, int(oldest_ts + RATE_LIMIT_WINDOW.total_seconds() - now_ts))
        else:
            retry_after = int(RATE_LIMIT_WINDOW.total_seconds())

        logger.warning(
            "approval rate-limit BLOCK | actor=%s count=%d limit=%d backend=redis",
            actor, count, RATE_LIMIT_MAX,
        )
        return [ConstraintViolation(
            field="actor",
            rule="actor_rate_limited",
            actual_value=f"{count} actions in last {RATE_LIMIT_WINDOW.total_seconds():.0f}s",
            expected_constraint=(
                f"at most {RATE_LIMIT_MAX} actions per "
                f"{RATE_LIMIT_WINDOW.total_seconds():.0f}s"
            ),
            severity="error",
        )], retry_after

    return [], None


async def _record_redis(
    actor: str, claim_id: int, action: str, reason_hash: str,
    result: dict[str, Any], now: datetime,
) -> None:
    key     = _idemp_key(actor, claim_id, action, reason_hash)
    rate_k  = _rate_key(actor)
    now_ts  = now.timestamp()
    ttl_s   = int(IDEMPOTENCY_TTL.total_seconds())
    window_s = int(RATE_LIMIT_WINDOW.total_seconds())

    pipe = _redis_client.pipeline()
    pipe.set(key, json.dumps(result, default=str), ex=ttl_s)
    pipe.zadd(rate_k, {f"{now_ts}": now_ts})
    pipe.expire(rate_k, window_s + 5)   # small buffer over the window
    await pipe.execute()


# ── In-memory fallback ────────────────────────────────────────────

async def _check_idempotency(
    actor: str, claim_id: int, action: str, reason_hash: str,
) -> dict[str, Any] | None:
    if _redis_client is not None:
        return await _check_idempotency_redis(actor, claim_id, action, reason_hash)
    return _check_idempotency_local(actor, claim_id, action, reason_hash)


async def _check_rate_limit(
    actor: str,
) -> tuple[list[ConstraintViolation], int | None]:
    if _redis_client is not None:
        return await _check_rate_limit_redis(actor)
    return _check_rate_limit_local(actor)


def _check_idempotency_local(
    actor: str, claim_id: int, action: str, reason_hash: str,
) -> dict[str, Any] | None:
    key = (actor, claim_id, action, reason_hash)
    now = datetime.now(timezone.utc)
    with _lock:
        entry = _idempotency_cache.get(key)
        if entry is None:
            return None
        if now - entry.timestamp > IDEMPOTENCY_TTL:
            _idempotency_cache.pop(key, None)
            return None
        return entry.result


def _check_rate_limit_local(
    actor: str,
) -> tuple[list[ConstraintViolation], int | None]:
    now    = datetime.now(timezone.utc)
    cutoff = now - RATE_LIMIT_WINDOW

    with _lock:
        dq = _rate_windows.get(actor)
        if dq is None:
            return [], None
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= RATE_LIMIT_MAX:
            retry_after = max(1, int((dq[0] + RATE_LIMIT_WINDOW - now).total_seconds()))
            logger.warning(
                "approval rate-limit BLOCK | actor=%s count=%d limit=%d backend=in-memory",
                actor, len(dq), RATE_LIMIT_MAX,
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


def _record_local(
    actor: str, claim_id: int, action: str, reason_hash: str,
    result: dict[str, Any], now: datetime,
) -> None:
    key = (actor, claim_id, action, reason_hash)
    with _lock:
        _idempotency_cache[key] = _IdempotencyEntry(timestamp=now, result=result)
        _rate_windows.setdefault(actor, deque()).append(now)


def _evict_expired(now: datetime) -> None:
    """Opportunistic cleanup of stale in-memory entries."""
    cutoff_idemp = now - IDEMPOTENCY_TTL
    cutoff_rate  = now - RATE_LIMIT_WINDOW
    with _lock:
        expired = [k for k, v in _idempotency_cache.items() if v.timestamp < cutoff_idemp]
        for k in expired:
            _idempotency_cache.pop(k, None)
        empty_actors = []
        for actor, dq in _rate_windows.items():
            while dq and dq[0] < cutoff_rate:
                dq.popleft()
            if not dq:
                empty_actors.append(actor)
        for a in empty_actors:
            _rate_windows.pop(a, None)


# ── Reason format checks (synchronous) ───────────────────────────

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


# ── HTTP status mapping helper ────────────────────────────────────

def http_status_for(violations: list[ConstraintViolation]) -> int:
    """Map a list of approval violations to the right HTTP status code."""
    codes = {v.rule for v in violations}
    if "actor_rate_limited" in codes:
        return 429
    return 422
