"""Shared database engine for the Claims MCP server.

Identical pattern to legacy-provider/db.py — single source of truth for
DB connection logic.

Rules applied:
    Rule 8  — DATABASE_URL read from .env, never hardcoded
    Rule 9  — Structured logging; no print()
    Rule 11 — Type hints on every function
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

logger = logging.getLogger(__name__)


def get_engine() -> Engine:
    """Create a SQLAlchemy engine from DATABASE_URL.

    Returns:
        Engine connected to Neon PostgreSQL.

    Raises:
        ValueError: If DATABASE_URL is not set.
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise ValueError(
            "DATABASE_URL is not set. "
            "Copy .env.example to .env and fill in your Neon credentials."
        )
    engine = create_engine(db_url, echo=False, pool_pre_ping=True)
    logger.debug("DB engine created (Claims MCP)")
    return engine
