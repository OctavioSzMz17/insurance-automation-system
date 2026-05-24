"""Redis async client factory — Fix A.

Creates a single redis.asyncio.Redis client from the REDIS_URL setting.
Returns None when REDIS_URL is absent or empty so callers can fall back
to in-memory stores without raising an error.

Rules applied:
    Rule 8  — URL read exclusively from Settings / .env; never hardcoded
    Rule 9  — Structured logging on connect/fail
    Rule 11 — Type hints throughout
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def create_redis_client(redis_url: str) -> Optional[object]:
    """Return an initialised redis.asyncio.Redis client, or None.

    Args:
        redis_url: Value of settings.redis_url (may be empty string).

    Returns:
        A connected redis.asyncio.Redis instance, or None if the URL is
        not configured or the connection fails (fail-open: the app keeps
        running with in-memory fallback).
    """
    if not redis_url:
        return None

    try:
        import redis.asyncio as aioredis  # type: ignore[import]
    except ImportError:
        logger.warning(
            "redis package not installed — running with in-memory stores. "
            "Install with: pip install 'redis[asyncio]'"
        )
        return None

    try:
        client = aioredis.from_url(
            redis_url,
            decode_responses=False,   # we handle bytes → str explicitly
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
        )
        # Verify the connection is reachable before handing it to callers.
        await client.ping()
        logger.info("Redis ping OK | connected")
        return client
    except Exception as exc:
        logger.error(
            "Redis connection failed — falling back to in-memory stores | err=%r", exc,
        )
        return None
