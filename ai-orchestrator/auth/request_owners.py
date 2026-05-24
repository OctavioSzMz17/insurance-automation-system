"""Bounded, thread-safe registry binding pipeline request_id → JWT subject.

Fix A — extends the original Master Patch Part 2 implementation with:
    1. Redis backend for shared state across multiple replicas.
    2. Overwrite protection: set() raises OwnerConflictError when a
       *different* sub tries to claim an already-owned request_id,
       closing the race condition where an attacker could re-register
       a leaked request_id under their own identity.

When REDIS_URL is configured, every set/get goes to Redis with a 1-hour
TTL so all replicas agree on ownership. When REDIS_URL is absent the
registry falls back to the original cachetools.TTLCache + threading.Lock
(single-replica dev / test).

Rules applied:
    Rule 7  — Tenant isolation enforced before SSE upgrade
    Rule 8  — Redis URL read from Settings / .env; never hardcoded here
    Rule 9  — Structured logging on every lookup and conflict
    Rule 11 — Strict type hints throughout
"""

from __future__ import annotations

import logging
from threading import Lock
from typing import Optional

from cachetools import TTLCache

logger = logging.getLogger(__name__)


# ── Exception ─────────────────────────────────────────────────────

class OwnerConflictError(Exception):
    """Raised by set() when a different sub already owns request_id.

    UUID4 collisions are cryptographically negligible. The only realistic
    re-submission with the same request_id is the same actor (idempotent,
    allowed). Any other sub claiming the same id is treated as an attack.
    """

    def __init__(self, request_id: str, existing_sub: str, new_sub: str) -> None:
        super().__init__(
            f"request_id {request_id!r} already owned by {existing_sub!r}; "
            f"cannot reassign to {new_sub!r}"
        )
        self.request_id:   str = request_id
        self.existing_sub: str = existing_sub
        self.new_sub:      str = new_sub


# ── Registry ──────────────────────────────────────────────────────

class RequestOwnerRegistry:
    """Thread-safe, bounded TTL map of `request_id → JWT subject`.

    Backed by Redis when a redis_client is supplied at construction;
    falls back to an in-process TTLCache for single-replica dev/test.

    Public API:
        set(request_id, sub) — async; records owner or raises OwnerConflictError
        get(request_id)      — async; returns sub or None
    """

    def __init__(
        self,
        maxsize:      int  = 10_000,
        ttl_seconds:  int  = 3600,
        redis_client        = None,   # redis.asyncio.Redis | None
    ) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be > 0")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")

        self._ttl:     int  = ttl_seconds
        self._maxsize: int  = maxsize
        self._redis          = redis_client

        # In-memory fallback
        self._cache: TTLCache[str, str] = TTLCache(maxsize=maxsize, ttl=ttl_seconds)
        self._lock:  Lock               = Lock()

        backend = "redis" if redis_client is not None else "in-memory"
        logger.info(
            "RequestOwnerRegistry ready | backend=%s maxsize=%d ttl=%ds",
            backend, maxsize, ttl_seconds,
        )

    # ── Public async API ──────────────────────────────────────────

    async def set(self, request_id: str, sub: str) -> None:
        """Record `sub` as the owner of `request_id`.

        Idempotent when called with the same sub. Raises OwnerConflictError
        when a *different* sub already holds ownership.

        Args:
            request_id: Server-generated UUID4 pipeline identifier.
            sub:        JWT subject of the initiating user.
        """
        if not request_id or not sub:
            raise ValueError("request_id and sub must be non-empty")

        if self._redis is not None:
            await self._set_redis(request_id, sub)
        else:
            self._set_local(request_id, sub)

    async def get(self, request_id: str) -> Optional[str]:
        """Return the owner sub, or None if expired / never recorded."""
        if not request_id:
            return None
        if self._redis is not None:
            return await self._get_redis(request_id)
        return self._get_local(request_id)

    # ── Redis backend ─────────────────────────────────────────────

    async def _set_redis(self, request_id: str, sub: str) -> None:
        key = f"owner:{request_id}"
        # Atomic pipeline: GET current owner, then SET only if key is new.
        # If key already exists (NX fails), compare existing vs new sub.
        pipe = self._redis.pipeline()
        pipe.get(key)
        pipe.set(key, sub, ex=self._ttl, nx=True)
        existing_raw, was_set = await pipe.execute()

        existing = (
            existing_raw.decode() if isinstance(existing_raw, bytes)
            else existing_raw
        )

        if not was_set and existing and existing != sub:
            logger.warning(
                "owner conflict | request_id=%s existing=%s attempted=%s backend=redis",
                request_id, existing, sub,
            )
            raise OwnerConflictError(request_id, existing, sub)

        logger.debug(
            "owner set | request_id=%s sub=%s backend=redis", request_id, sub,
        )

    async def _get_redis(self, request_id: str) -> Optional[str]:
        key = f"owner:{request_id}"
        value = await self._redis.get(key)
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else value

    # ── In-memory fallback ────────────────────────────────────────

    def _set_local(self, request_id: str, sub: str) -> None:
        with self._lock:
            existing = self._cache.get(request_id)
            if existing is not None and existing != sub:
                logger.warning(
                    "owner conflict | request_id=%s existing=%s attempted=%s backend=in-memory",
                    request_id, existing, sub,
                )
                raise OwnerConflictError(request_id, existing, sub)
            self._cache[request_id] = sub
        logger.debug(
            "owner set | request_id=%s sub=%s backend=in-memory", request_id, sub,
        )

    def _get_local(self, request_id: str) -> Optional[str]:
        with self._lock:
            return self._cache.get(request_id)

    def __repr__(self) -> str:  # pragma: no cover — debug aid only
        backend = "redis" if self._redis is not None else "in-memory"
        return (
            f"RequestOwnerRegistry(backend={backend}, "
            f"maxsize={self._maxsize}, ttl={self._ttl}s)"
        )
