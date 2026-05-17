"""Typed configuration for the orchestrator (Phase C).

Reads .env via pydantic-settings. Provides one Settings singleton that
the rest of the orchestrator imports. Never re-read env vars directly
from code outside this module (Rule 8).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """All orchestrator configuration in one place."""

    model_config = SettingsConfigDict(
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── API ────────────────────────────────────────────────────────
    api_host: str = Field(default="0.0.0.0",  alias="API_HOST")
    api_port: int = Field(default=8000,        alias="API_PORT")
    environment: str = Field(default="development", alias="ENVIRONMENT")
    log_level: str = Field(default="INFO",     alias="LOG_LEVEL")

    # ── LLM (used indirectly via core/models/factory.py) ──────────
    llm_provider:         str = Field(default="claude", alias="LLM_PROVIDER")
    llm_provider_api_key: str = Field(default="",       alias="LLM_PROVIDER_API_KEY")
    model_name:           str = Field(default="",       alias="MODEL_NAME")

    # ── Database (only used by MCPs; orchestrator never queries DB
    #              directly — Rule 2)
    database_url: str = Field(default="", alias="DATABASE_URL")

    # ── Agent / pipeline ──────────────────────────────────────────
    max_retry_attempts: int = Field(default=3, alias="MAX_RETRY_ATTEMPTS")

    # ── JWT / RBAC (Rule 7) ───────────────────────────────────────
    jwt_secret_key:       str = Field(default="dev-only-replace-me", alias="JWT_SECRET_KEY")
    jwt_algorithm:        str = Field(default="HS256",                alias="JWT_ALGORITHM")
    jwt_expiration_hours: int = Field(default=24,                     alias="JWT_EXPIRATION_HOURS")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings instance (cached for app lifetime)."""
    return Settings()
