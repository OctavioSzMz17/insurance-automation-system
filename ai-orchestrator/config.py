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

    # ── MCP server SSE endpoints (Rule 7 — all MCP comms over SSE) ─
    # Defaults point to localhost ports for direct-run dev.
    # Docker/K8s override these with container-network URLs via .env.
    users_mcp_url:    str = Field(default="http://localhost:8004/sse", alias="MCP_USERS_URL")
    policies_mcp_url: str = Field(default="http://localhost:8003/sse", alias="MCP_POLICIES_URL")
    claims_mcp_url:   str = Field(default="http://localhost:8002/sse", alias="MCP_CLAIMS_URL")
    documents_mcp_url:str = Field(default="http://localhost:8005/sse", alias="MCP_DOCUMENTS_URL")
    legacy_mcp_url:   str = Field(default="http://localhost:8006/sse", alias="MCP_LEGACY_URL")

    # ── Redis (Fix A — shared state across replicas) ──────────────
    # When set, ownership registry, stream tickets, and approval
    # idempotency/rate-limit caches use Redis instead of in-process
    # TTLCache/dicts. Required for multi-replica deployments.
    # If absent, falls back to in-memory (single-replica / dev only).
    redis_url: str = Field(default="", alias="REDIS_URL")

    # Master Patch Part 1 — shared HMAC secret for MCP transport auth.
    # Validated by every MCP server's MCPAuthMiddleware before the SSE
    # upgrade. Generate with: openssl rand -hex 32 (>=32 chars required).
    # Closes the post-Fix-1 gap where MCP servers accepted any HTTP client.
    mcp_shared_secret: str = Field(default="", alias="MCP_SHARED_SECRET")

    # Master Patch Part 3 — HMAC-SHA256 key for signing trace_id markers
    # written into claims.ai_reasoning. The orchestrator signs every
    # marker before persistence; the claims MCP's append_reasoning tool
    # rejects any caller-controlled text containing unsigned markers,
    # closing the audit-trail spoofing vector. >=32 chars required.
    proposal_signing_key: str = Field(default="", alias="PROPOSAL_SIGNING_KEY")

    # ── File storage (Fix #73) ────────────────────────────────────
    # Persistent directory for uploaded PDFs. Must be a volume mount in
    # production — /tmp is cleared on pod restart. App raises RuntimeError
    # at startup if this is /tmp (or empty) in production/staging.
    # Set via CLAIM_UPLOAD_DIR in .env.
    claim_upload_dir: str = Field(
        default="/tmp/claim-uploads",
        alias="CLAIM_UPLOAD_DIR",
    )

    # ── Agent / pipeline ──────────────────────────────────────────
    max_retry_attempts: int = Field(default=3, alias="MAX_RETRY_ATTEMPTS")

    # ── CORS (Fix #66) ────────────────────────────────────────────
    # Comma-separated list of allowed browser origins stored as a raw
    # string (pydantic-settings tries JSON-decode on list[str] fields
    # before validators run, breaking comma-separated values).
    # Use settings.cors_origins_list for the parsed list.
    # Example: CORS_ALLOWED_ORIGINS=http://localhost:3000,https://app.insureai.io
    cors_allowed_origins: str = Field(
        default="http://localhost:3000,http://localhost:8000",
        alias="CORS_ALLOWED_ORIGINS",
    )

    @property
    def cors_origins_list(self) -> list[str]:
        """Return CORS_ALLOWED_ORIGINS as a parsed list of stripped strings."""
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    # ── JWT / RBAC (Rule 7) ───────────────────────────────────────
    jwt_secret_key:       str = Field(default="dev-only-replace-me", alias="JWT_SECRET_KEY")
    jwt_algorithm:        str = Field(default="HS256",                alias="JWT_ALGORITHM")
    jwt_expiration_hours: int = Field(default=24,                     alias="JWT_EXPIRATION_HOURS")

    # Optional aud/iss claims validated by decode_jwt when non-empty.
    # Set in production to bind tokens to this specific service.
    jwt_audience: str = Field(default="", alias="JWT_AUDIENCE")
    jwt_issuer:   str = Field(default="", alias="JWT_ISSUER")

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
