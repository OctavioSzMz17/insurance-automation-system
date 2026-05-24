"""Bounded SSE stream-ticket store (Master Patch Part 4 + Fix A).

Fix A adds a Redis backend so tickets survive across multiple replicas.
When REDIS_URL is configured, mint/pop operate on Redis keys with a 30s
TTL. When REDIS_URL is absent, falls back to the original cachetools
TTLCache + threading.Lock (single-replica dev / test).

Ticket payload stored in Redis: JSON with sub, role, request_id,
expires_at (monotonic approximated via wall-clock offset).

Rules applied:
    Rule 7  — Auth-layer infrastructure owned by ai-orchestrator/auth
    Rule 8  — Redis URL read from Settings / .env; never hardcoded
    Rule 9  — Every quota denial emits a structured log line
    Rule 11 — Strict type hints throughout
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from threading import Lock
from typing import Optional

from cachetools import TTLCache

from .roles import Role

logger = logging.getLogger(__name__)


# ── Exception ─────────────────────────────────────────────────────

class TicketQuotaExceeded(Exception):
    """Raised when a sub tries to hold more than `max_per_sub` tickets."""

    def __init__(
        self,
        sub:           str,
        current_count: int,
        cap:           int,
        retry_after_s: int,
    ) -> None:
        super().__init__(
            f"Subject {sub!r} holds {current_count} stream tickets "
            f"(cap={cap}); retry_after={retry_after_s}s"
        )
        self.sub:           str = sub
        self.current_count: int = current_count
        self.cap:           int = cap
        self.retry_after_s: int = retry_after_s


# ── Ticket value object ───────────────────────────────────────────

@dataclass
class StreamTicket:
    """One unconsumed SSE auth ticket."""
    request_id: str
    sub:        str
    role:       Role
    expires_at: float   # time.monotonic() deadline


# ── Store ─────────────────────────────────────────────────────────

class StreamTicketStore:
    """Bounded TTL ticket store with per-sub quotas.

    Backed by Redis when a redis_client is supplied; falls back to
    cachetools.TTLCache + threading.Lock for single-replica dev/test.

    Redis key layout:
        ticket:{ticket_id}          → JSON payload, TTL = ttl_seconds
        ticket_sub:{sub}:{ticket_id}→ marker key for per-sub counting,
                                      same TTL
    """

    def __init__(
        self,
        maxsize:      int  = 10_000,
        ttl_seconds:  int  = 30,
        max_per_sub:  int  = 20,
        redis_client        = None,   # redis.asyncio.Redis | None
    ) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be > 0")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        if max_per_sub <= 0:
            raise ValueError("max_per_sub must be > 0")

        self._ttl:         int  = ttl_seconds
        self._max_per_sub: int  = max_per_sub
        self._maxsize:     int  = maxsize
        self._redis              = redis_client

        # In-memory fallback
        self._cache: TTLCache[str, StreamTicket] = TTLCache(
            maxsize=maxsize, ttl=ttl_seconds,
        )
        self._lock: Lock = Lock()

        backend = "redis" if redis_client is not None else "in-memory"
        logger.info(
            "StreamTicketStore ready | backend=%s maxsize=%d ttl=%ds max_per_sub=%d",
            backend, maxsize, ttl_seconds, max_per_sub,
        )

    # ── Public async API ──────────────────────────────────────────

    async def mint(self, sub: str, role: Role, request_id: str) -> str:
        """Mint a single-use ticket. Returns ticket_id (UUID4).

        Raises TicketQuotaExceeded if the subject already holds the
        maximum number of outstanding tickets.
        """
        if not sub or not request_id:
            raise ValueError("sub and request_id must be non-empty")

        if self._redis is not None:
            return await self._mint_redis(sub, role, request_id)
        return self._mint_local(sub, role, request_id)

    async def pop(self, ticket_id: str) -> Optional[StreamTicket]:
        """Atomically burn a ticket. Returns StreamTicket or None."""
        if not ticket_id:
            return None
        if self._redis is not None:
            return await self._pop_redis(ticket_id)
        return self._pop_local(ticket_id)

    # ── Redis backend ─────────────────────────────────────────────

    async def _mint_redis(self, sub: str, role: Role, request_id: str) -> str:
        # Fix #68 — count live marker keys via non-blocking SCAN cursor loop.
        # KEYS is O(N) and blocks Redis; SCAN spreads the cost over iterations.
        pattern = f"ticket_sub:{sub}:*"
        current = 0
        cursor = 0
        while True:
            cursor, keys = await self._redis.scan(
                cursor=cursor, match=pattern, count=100,
            )
            current += len(keys)
            if cursor == 0:
                break

        if current >= self._max_per_sub:
            logger.warning(
                "stream-ticket DENIED quota | sub=%s current=%d cap=%d backend=redis",
                sub, current, self._max_per_sub,
            )
            raise TicketQuotaExceeded(
                sub=sub, current_count=current,
                cap=self._max_per_sub, retry_after_s=self._ttl,
            )

        ticket_id  = str(uuid.uuid4())
        expires_at = time.monotonic() + self._ttl
        payload = json.dumps({
            "request_id": request_id,
            "sub":        sub,
            "role":       role.value,
            "expires_at": expires_at,
        })

        pipe = self._redis.pipeline()
        pipe.set(f"ticket:{ticket_id}", payload, ex=self._ttl)
        pipe.set(f"ticket_sub:{sub}:{ticket_id}", "1", ex=self._ttl)
        await pipe.execute()

        logger.debug(
            "stream-ticket minted | ticket=%s sub=%s backend=redis", ticket_id[:8], sub,
        )
        return ticket_id

    async def _pop_redis(self, ticket_id: str) -> Optional[StreamTicket]:
        key = f"ticket:{ticket_id}"
        pipe = self._redis.pipeline()
        pipe.get(key)
        pipe.delete(key)
        raw, _ = await pipe.execute()

        if raw is None:
            return None

        try:
            data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        except (json.JSONDecodeError, AttributeError):
            return None

        # Clean up the sub-marker key (best-effort)
        sub = data.get("sub", "")
        await self._redis.delete(f"ticket_sub:{sub}:{ticket_id}")

        return StreamTicket(
            request_id=data["request_id"],
            sub=sub,
            role=Role(data["role"]),
            expires_at=data["expires_at"],
        )

    # ── In-memory fallback ────────────────────────────────────────

    def _mint_local(self, sub: str, role: Role, request_id: str) -> str:
        with self._lock:
            self._cache.expire()
            current = self._count_for_sub_locked(sub)
            if current >= self._max_per_sub:
                logger.warning(
                    "stream-ticket DENIED quota | sub=%s current=%d cap=%d backend=in-memory",
                    sub, current, self._max_per_sub,
                )
                raise TicketQuotaExceeded(
                    sub=sub, current_count=current,
                    cap=self._max_per_sub, retry_after_s=self._ttl,
                )
            ticket_id = str(uuid.uuid4())
            self._cache[ticket_id] = StreamTicket(
                request_id=request_id,
                sub=sub,
                role=role,
                expires_at=time.monotonic() + self._ttl,
            )
            return ticket_id

    def _pop_local(self, ticket_id: str) -> Optional[StreamTicket]:
        with self._lock:
            self._cache.expire()
            return self._cache.pop(ticket_id, None)

    def _count_for_sub_locked(self, sub: str) -> int:
        n = 0
        for ticket in self._cache.values():
            if ticket.sub == sub:
                n += 1
                if n >= self._max_per_sub:
                    return n
        return n

    def __repr__(self) -> str:  # pragma: no cover — debug aid only
        backend = "redis" if self._redis is not None else "in-memory"
        return (
            f"StreamTicketStore(backend={backend}, "
            f"maxsize={self._maxsize}, ttl={self._ttl}s, "
            f"max_per_sub={self._max_per_sub})"
        )
