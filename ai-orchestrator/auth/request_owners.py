"""Bounded, thread-safe registry binding pipeline request_id → JWT subject.

Backed by Redis when a redis_client is supplied; falls back to cachetools
TTLCache for single-replica dev/test.
"""
from __future__ import annotations

import logging
from threading import Lock
from typing import Optional

from cachetools import TTLCache

logger = logging.getLogger(__name__)


class OwnerConflictError(Exception):
    def __init__(self, request_id: str, existing_sub: str, new_sub: str) -> None:
        super().__init__(
            f"request_id {request_id!r} already owned by {existing_sub!r}; "
            f"cannot reassign to {new_sub!r}"
        )
        self.request_id = request_id
        self.existing_sub = existing_sub
        self.new_sub = new_sub


class RequestOwnerRegistry:
    def __init__(self, maxsize: int = 10_000, ttl_seconds: int = 3600, redis_client=None) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be > 0")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")

        self._ttl = ttl_seconds
        self._maxsize = maxsize
        self._redis = redis_client
        self._cache: TTLCache[str, str] = TTLCache(maxsize=maxsize, ttl=ttl_seconds)
        self._lock = Lock()
        logger.info(
            "RequestOwnerRegistry ready | backend=%s",
            "redis" if redis_client else "in-memory",
        )

    async def set(self, request_id: str, sub: str) -> None:
        if not request_id or not sub:
            raise ValueError("request_id and sub must be non-empty")
        if self._redis is not None:
            await self._set_redis(request_id, sub)
        else:
            self._set_local(request_id, sub)

    async def get(self, request_id: str) -> Optional[str]:
        if not request_id:
            return None
        if self._redis is not None:
            return await self._get_redis(request_id)
        return self._get_local(request_id)

    async def _set_redis(self, request_id: str, sub: str) -> None:
        key = f"owner:{request_id}"
        pipe = self._redis.pipeline()
        pipe.get(key)
        pipe.set(key, sub, ex=self._ttl, nx=True)
        existing_raw, was_set = await pipe.execute()
        existing = existing_raw.decode() if isinstance(existing_raw, bytes) else existing_raw
        if not was_set and existing and existing != sub:
            raise OwnerConflictError(request_id, existing, sub)

    async def _get_redis(self, request_id: str) -> Optional[str]:
        value = await self._redis.get(f"owner:{request_id}")
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else value

    def _set_local(self, request_id: str, sub: str) -> None:
        with self._lock:
            existing = self._cache.get(request_id)
            if existing is not None and existing != sub:
                raise OwnerConflictError(request_id, existing, sub)
            self._cache[request_id] = sub

    def _get_local(self, request_id: str) -> Optional[str]:
        with self._lock:
            return self._cache.get(request_id)
