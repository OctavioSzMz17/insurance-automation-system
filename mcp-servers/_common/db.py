"""Async SQLAlchemy engine singleton. Double-checked init, one engine per process (Rule 2).

`create_async_engine` is NEVER called inside a tool handler. Pool size is bounded so a runaway
concurrent burst cannot exhaust the DB connection pool.
"""
from __future__ import annotations

import asyncio
from typing import Any, Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from .logging import get_logger

_logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[object] | None = None
_lock: Final[asyncio.Lock] = asyncio.Lock()


_SSL_REQUIRE_MODES = frozenset({"require", "verify-ca", "verify-full"})


def _normalize_dsn(dsn: str) -> tuple[str, dict[str, Any]]:
    """Convert a psycopg-style DSN to asyncpg-friendly form.

    asyncpg rejects ``sslmode`` / ``channel_binding`` query params. We strip them and
    translate ``sslmode=require`` (and stricter modes) into ``connect_args={"ssl": "require"}``.
    """
    parts = urlsplit(dsn)
    scheme = "postgresql+asyncpg" if parts.scheme == "postgresql" else parts.scheme
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    connect_args: dict[str, Any] = {}
    kept: list[tuple[str, str]] = []
    for k, v in query_pairs:
        key = k.lower()
        if key == "sslmode":
            if v.lower() in _SSL_REQUIRE_MODES:
                connect_args["ssl"] = "require"
            continue
        if key == "channel_binding":
            # asyncpg negotiates this automatically; the query-string form is psycopg-only.
            continue
        kept.append((k, v))
    new_query = urlencode(kept)
    rebuilt = urlunsplit((scheme, parts.netloc, parts.path, new_query, parts.fragment))
    return rebuilt, connect_args


async def get_engine(dsn: str) -> AsyncEngine:
    """Return the process-wide engine. Builds it under a lock on first call."""
    global _engine, _sessionmaker
    if _engine is not None:
        return _engine
    async with _lock:
        if _engine is not None:  # second check inside the lock
            return _engine
        normalized, connect_args = _normalize_dsn(dsn)
        engine = create_async_engine(
            normalized,
            pool_size=10,
            max_overflow=5,
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args=connect_args,
        )
        _engine = engine
        _sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        _logger.info("db engine initialized", extra={"event": "db.engine.init"})
        return engine


async def get_sessionmaker(dsn: str) -> async_sessionmaker[object]:
    await get_engine(dsn)
    if _sessionmaker is None:
        raise RuntimeError("sessionmaker not initialized after get_engine")
    return _sessionmaker


async def dispose_engine() -> None:
    """Call from lifespan shutdown."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None
        _logger.info("db engine disposed", extra={"event": "db.engine.dispose"})


async def ping(dsn: str) -> bool:
    """Real connectivity check used by /internal/ready (Rule 7)."""
    from sqlalchemy import text

    engine = await get_engine(dsn)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return True
