"""Redis sliding-window rate limiter (Phase 4 — Rule 8).

Limits each authenticated user (JWT sub) or IP address to
RATE_LIMIT_REQUESTS requests per RATE_LIMIT_WINDOW_SECONDS.

Algorithm: sorted-set sliding window.
  Key:     rate:sub:{sub}  or  rate:ip:{ip}
  Members: "{timestamp}-{random_suffix}"  (unique per request)
  Score:   Unix timestamp (float)

Each request:
  1. Prune entries older than window_start.
  2. Add the current request member.
  3. ZCARD → current count.
  4. If count > limit → 429 + Retry-After header.

Fail-open: if Redis is unavailable, the request is allowed through and a
warning is logged. This prevents a Redis outage from taking down the API.

Sub extraction uses an *unverified* JWT peek (base64 decode only) so we
don't pay double crypto overhead — actual auth is enforced by require_role()
on the individual endpoints.
"""
from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from typing import Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

_RATE_LIMIT_EXCEEDED = (
    '{"detail": "Rate limit exceeded. Please try again later."}'
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _peek_jwt_sub(token: str) -> Optional[str]:
    """Decode JWT payload without signature verification (rate-key use only)."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1]
        # Restore base64 padding
        padding = (4 - len(payload_b64) % 4) % 4
        payload = json.loads(base64.b64decode(payload_b64 + "=" * padding))
        sub = payload.get("sub")
        return str(sub) if sub else None
    except Exception:
        return None


def _get_client_ip(request: Request) -> str:
    """Return client IP, honouring X-Forwarded-For for proxied deployments."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_key(request: Request) -> str:
    """Derive the rate-limit key: sub (from JWT peek) or IP fallback."""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        sub = _peek_jwt_sub(auth[7:])
        if sub:
            return f"rate:sub:{sub}"
    return f"rate:ip:{_get_client_ip(request)}"


async def check_sliding_window(
    redis,
    key: str,
    limit: int,
    window: int,
) -> tuple[bool, int]:
    """Atomic sliding-window check.

    Returns (allowed, count_in_window_after_this_request).
    Rejected requests still consume a slot so burst-attack entries age out
    naturally with the window TTL — the sorted set stays bounded.
    """
    now = time.time()
    window_start = now - window
    member = f"{now:.6f}-{uuid.uuid4().hex[:8]}"

    pipe = redis.pipeline()
    pipe.zremrangebyscore(key, "-inf", window_start)   # prune stale entries
    pipe.zadd(key, {member: now})                       # add current request
    pipe.zcard(key)                                     # count in window
    pipe.expire(key, window + 1)                        # auto-cleanup TTL
    results = await pipe.execute()

    count: int = results[2]
    return count <= limit, count


# ── Middleware ────────────────────────────────────────────────────────────────


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-user (sub) or per-IP sliding-window rate limiter."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        redis = getattr(request.app.state, "redis", None)
        settings = getattr(request.app.state, "settings", None)

        # Skip rate limiting when Redis or settings are not yet available.
        if redis is None or settings is None:
            return await call_next(request)

        key = _rate_key(request)
        limit: int = settings.rate_limit_requests
        window: int = settings.rate_limit_window_seconds

        try:
            allowed, count = await check_sliding_window(redis, key, limit, window)
        except Exception as exc:
            logger.warning("Rate limit check failed — fail-open | key=%s err=%s", key, exc)
            return await call_next(request)

        if not allowed:
            logger.warning(
                "Rate limit exceeded | key=%s count=%d limit=%d window=%ds",
                key, count, limit, window,
            )
            return Response(
                content=_RATE_LIMIT_EXCEEDED,
                status_code=429,
                headers={
                    "Content-Type": "application/json",
                    "Retry-After": str(window),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time()) + window),
                },
            )

        response = await call_next(request)
        remaining = max(0, limit - count)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(time.time()) + window)
        return response
