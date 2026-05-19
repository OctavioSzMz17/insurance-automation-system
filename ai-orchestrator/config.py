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

    # D3.1 — log formatter selection.
    # "text" → human-readable pipe-separated format (dev default).
    # "json" → one JSON object per line (production / aggregators).
    log_format: str = Field(default="text",    alias="LOG_FORMAT")

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

    # ── Observability — Langfuse self-hosted (Phase D1.2) ────────
    # Set all three to opt-in. When `langfuse_public_key` is empty the
    # tracer factory returns a no-op, so the app keeps working with
    # no telemetry overhead and no failures.
    langfuse_host:       str = Field(default="", alias="LANGFUSE_HOST")
    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")
    # Browser-accessible URL (D5.1 deep-links); falls back to host if empty.
    langfuse_public_url: str = Field(default="", alias="LANGFUSE_PUBLIC_URL")

    @property
    def langfuse_enabled(self) -> bool:
        """True iff all three Langfuse settings are populated."""
        return bool(
            self.langfuse_host
            and self.langfuse_public_key
            and self.langfuse_secret_key
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings instance (cached for app lifetime)."""
    return Settings()
