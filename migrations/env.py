"""Alembic env. Reads DATABASE_URL from environment, never hardcoded (Rule 8)."""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set; refusing to run migrations")
    # Alembic uses the sync driver, and the driver is named explicitly rather than
    # left to SQLAlchemy's default. SQLAlchemy 2.1 changed what bare `postgresql://`
    # resolves to — psycopg (v3) instead of psycopg2 — and requirements.in allows
    # `SQLAlchemy>=2.0,<3`, so a future lock regeneration would otherwise break
    # migrations with `ModuleNotFoundError: No module named 'psycopg'`.
    for prefix in ("postgresql+asyncpg://", "postgresql://"):
        if dsn.startswith(prefix):
            return "postgresql+psycopg2://" + dsn[len(prefix):]
    return dsn


config.set_main_option("sqlalchemy.url", _resolve_dsn())

target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
