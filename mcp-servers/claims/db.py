"""Shared database engine for the Claims MCP server.

Fix #72 — engine is now a module-level singleton created once and reused
across all tool calls. Previously, create_engine() was called on every
invocation, discarding the pool and opening a new connection each time.
Under concurrent load this exhausted the Neon connection limit.

Double-checked locking (threading.Lock) ensures thread-safe lazy init
without the cost of acquiring the lock on every hot-path call.

Rules applied:
    Rule 8  — DATABASE_URL read from .env, never hardcoded
    Rule 9  — Structured logging; no print()
    Rule 11 — Type hints on every function
"""

import logging
import os
import threading
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_engine_lock = threading.Lock()


def get_engine() -> Engine:
    """Return the singleton SQLAlchemy engine, creating it on first call.

    Returns:
        Engine connected to Neon PostgreSQL (shared across all tool calls).

    Raises:
        ValueError: If DATABASE_URL is not set in the environment.
    """
    global _engine
    if _engine is not None:
        return _engine

    with _engine_lock:
        if _engine is not None:  # double-check after acquiring lock
            return _engine

        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            raise ValueError(
                "DATABASE_URL is not set. "
                "Copy .env.example to .env and fill in your Neon credentials."
            )
        _engine = create_engine(
            db_url,
            echo=False,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
        logger.info("DB engine created (Claims MCP | singleton)")
        return _engine
