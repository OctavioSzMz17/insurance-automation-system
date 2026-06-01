"""One-time SSE stream ticket store (Rule 7 — anti ?token=JWT).

Tickets are short-lived (TTL=30s) and single-use. A client requests a
ticket, receives a UUID, then connects to /sse?ticket=<uuid>. The server
atomically pops and validates it, refusing any second attempt.

Backed by Redis when a redis_client is supplied; in-memory TTLCache
fallback for single-replica dev/test.
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


class TicketQuotaExceeded(Exception):
    def __init__(self, sub: str, current_count: int, cap: int, retry_after_s: int) -> None:
        super().__init__(
            f"Subject {sub!r} holds {current_count} stream tickets (cap={cap}); "
            f"retry_after={retry_after_s}s"
        )
        self.sub = sub
        self.current_count = current_count
        self.cap = cap
        self.retry_after_s = retry_after_s


@dataclass
class StreamTicket:
    request_id: str
    sub: str
    role: Role
    expires_at: float  # wall-clock epoch seconds


class StreamTicketStore:
    def __init__(
        self,
        maxsize: int = 10_000,
        ttl_seconds: int = 30,
        max_per_sub: int = 20,
        redis_client=None,
    ) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be > 0")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        if max_per_sub <= 0:
            raise ValueError("max_per_sub must be > 0")

        self._ttl = ttl_seconds
        self._max_per_sub = max_per_sub
        self._maxsize = maxsize
        self._redis = redis_client
        self._cache: TTLCache[str, StreamTicket] = TTLCache(maxsize=maxsize, ttl=ttl_seconds)
        self._lock = Lock()
        logger.info(
            "StreamTicketStore ready | backend=%s ttl=%ds max_per_sub=%d",
            "redis" if redis_client else "in-memory",
            ttl_seconds,
            max_per_sub,
        )

    async def mint(self, sub: str, role: Role, request_id: str) -> str:
        if not sub or not request_id:
            raise ValueError("sub and request_id must be non-empty")
        if self._redis is not None:
            return await self._mint_redis(sub, role, request_id)
        return self._mint_local(sub, role, request_id)

    async def pop(self, ticket_id: str) -> Optional[StreamTicket]:
        if not ticket_id:
            return None
        if self._redis is not None:
            return await self._pop_redis(ticket_id)
        return self._pop_local(ticket_id)

    async def _mint_redis(self, sub: str, role: Role, request_id: str) -> str:
        pattern = f"ticket_sub:{sub}:*"
        current = 0
        cursor = 0
        while True:
            cursor, keys = await self._redis.scan(cursor=cursor, match=pattern, count=100)
            current += len(keys)
            if cursor == 0:
                break

        if current >= self._max_per_sub:
            logger.warning("stream-ticket quota exceeded | sub=%s current=%d", sub, current)
            raise TicketQuotaExceeded(sub, current, self._max_per_sub, self._ttl)

        ticket_id = str(uuid.uuid4())
        expires_at = time.time() + self._ttl
        payload = json.dumps({"request_id": request_id, "sub": sub, "role": role.value, "expires_at": expires_at})

        pipe = self._redis.pipeline()
        pipe.set(f"ticket:{ticket_id}", payload, ex=self._ttl)
        pipe.set(f"ticket_sub:{sub}:{ticket_id}", "1", ex=self._ttl)
        await pipe.execute()
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

        sub = data.get("sub", "")
        await self._redis.delete(f"ticket_sub:{sub}:{ticket_id}")
        return StreamTicket(
            request_id=data["request_id"],
            sub=sub,
            role=Role(data["role"]),
            expires_at=data["expires_at"],
        )

    def _mint_local(self, sub: str, role: Role, request_id: str) -> str:
        with self._lock:
            self._cache.expire()
            current = sum(1 for t in self._cache.values() if t.sub == sub)
            if current >= self._max_per_sub:
                raise TicketQuotaExceeded(sub, current, self._max_per_sub, self._ttl)
            ticket_id = str(uuid.uuid4())
            self._cache[ticket_id] = StreamTicket(
                request_id=request_id, sub=sub, role=role, expires_at=time.time() + self._ttl
            )
            return ticket_id

    def _pop_local(self, ticket_id: str) -> Optional[StreamTicket]:
        with self._lock:
            self._cache.expire()
            return self._cache.pop(ticket_id, None)
