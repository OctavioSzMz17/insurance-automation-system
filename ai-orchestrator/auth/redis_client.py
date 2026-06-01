"""Async Redis client factory for auth modules.

Returns None when REDIS_URL is absent so callers fall back to
in-memory stores without raising (fail-open for single-replica dev).
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def create_redis_client(redis_url: str) -> Optional[object]:
    if not redis_url:
        return None

    try:
        import redis.asyncio as aioredis
    except ImportError:
        logger.warning("redis package not installed — in-memory fallback active")
        return None

    try:
        client = aioredis.from_url(
            redis_url,
            decode_responses=False,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
        )
        await client.ping()
        logger.info("Auth Redis ping OK")
        return client
    except Exception as exc:
        logger.error("Auth Redis connection failed — in-memory fallback | err=%r", exc)
        return None
