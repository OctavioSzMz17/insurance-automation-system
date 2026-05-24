"""Shared database engine for the Users MCP server.

Fix #72 — engine is now a module-level singleton (see claims/db.py for
full rationale). Double-checked locking ensures thread-safe lazy init.

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
    """Return the singleton SQLAlchemy engine, creating it on first call."""
    global _engine
    if _engine is not None:
        return _engine

    with _engine_lock:
        if _engine is not None:
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
        logger.info("DB engine created (Users MCP | singleton)")
        return _engine
