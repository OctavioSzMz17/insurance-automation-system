"""Async Redis client (redis-py asyncio API). One pool per process.

All security-critical state lives in Redis (Rule 14). Use SCAN cursor loops, NEVER KEYS *.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Final

from redis.asyncio import Redis, from_url

from .logging import get_logger

_logger = get_logger(__name__)

_client: Redis | None = None
_lock: Final[asyncio.Lock] = asyncio.Lock()


async def get_client(url: str) -> Redis:
    global _client
    if _client is not None:
        return _client
    async with _lock:
        if _client is not None:
            return _client
        client = from_url(url, decode_responses=True, socket_timeout=5, socket_connect_timeout=5)
        await client.ping()
        _client = client
        _logger.info("redis client initialized", extra={"event": "redis.init"})
        return client


async def dispose_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        _logger.info("redis client disposed", extra={"event": "redis.dispose"})


async def ping(url: str) -> bool:
    client = await get_client(url)
    return bool(await client.ping())


async def scan_iter_keys(client: Redis, match: str, count: int = 100) -> AsyncIterator[str]:
    """SCAN cursor loop. Replacement for the forbidden KEYS *  (Rule 7, Rule 14)."""
    cursor = 0
    while True:
        cursor, batch = await client.scan(cursor=cursor, match=match, count=count)
        for key in batch:
            yield key
        if cursor == 0:
            return
