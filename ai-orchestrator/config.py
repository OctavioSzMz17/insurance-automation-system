"""Orchestrator settings. Fail-hard at startup on weak/missing secrets (Rule 8)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class OrchestratorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")

    # ── LLM tier registry ────────────────────────────────────────────────────
    low_model: str = Field(default="")
    medium_model: str = Field(default="")
    high_model: str = Field(default="")
    analyst_model_tier: str = Field(default="medium")

    anthropic_api_key: str = Field(default="")
    openai_api_key: str = Field(default="")
    gemini_api_key: str = Field(default="")
    local_base_url: str = Field(default="")
    local_api_key: str = Field(default="")

    # ── MCP endpoints ────────────────────────────────────────────────────────
    mcp_claims_url: str = Field(default="")
    mcp_policies_url: str = Field(default="")
    mcp_users_url: str = Field(default="")
    mcp_documents_url: str = Field(default="")
    mcp_shared_secret: str = Field(default="")

    # ── Proposal signing ─────────────────────────────────────────────────────
    hmac_proposal_key: str = Field(default="")

    # ── Redis ────────────────────────────────────────────────────────────────
    redis_url: str = Field(default="")

    # ── JWT / RBAC (Rule 7) ──────────────────────────────────────────────────
    # jwt_secret_key validated in cross_validate (not @field_validator) so
    # get_settings() can be called at import time without raising.
    jwt_secret_key: str = Field(default="dev-only-replace-me-at-least-32-chars-long")
    jwt_algorithm: str = Field(default="HS256")
    jwt_expiration_hours: int = Field(default=24, ge=1, le=720)
    jwt_audience: str = Field(default="")
    jwt_issuer: str = Field(default="")
    # Dev-only bootstrap endpoint protection (disabled in prod — Rule 8).
    dev_bootstrap_secret: str = Field(default="")

    # ── CORS ─────────────────────────────────────────────────────────────────
    # Comma-separated list; parsed via cors_origins_list property.
    cors_allowed_origins: str = Field(default="http://localhost:3000,http://localhost:8000")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    # ── Analyst loop limits ──────────────────────────────────────────────────
    analyst_max_steps: int = Field(default=10, ge=1, le=50)
    analyst_timeout_seconds: int = Field(default=60, ge=5, le=600)
    analyst_proposal_ttl_seconds: int = Field(default=300, ge=60, le=3600)

    # ── File upload (Phase 4) ────────────────────────────────────────────────
    # Must be a persistent volume mount in production — never /tmp (Rule 10).
    claim_upload_dir: str = Field(default="./data/uploads")
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1)

    # ── Rate limiting (Phase 4) ──────────────────────────────────────────────
    # Sliding window per user/IP. Fail-open when Redis is unavailable.
    rate_limit_requests: int = Field(default=60, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)

    # ── mTLS client certs (F4.7) ─────────────────────────────────────────────
    # Paths to certs used by the orchestrator when connecting to MCP servers.
    # All three must be set to enable mTLS; any empty value disables it (dev default).
    # Generate with: bash scripts/certs/gen_dev_certs.sh
    mcp_client_cert: str = Field(default="")   # MCP_CLIENT_CERT — orchestrator.crt
    mcp_client_key: str = Field(default="")    # MCP_CLIENT_KEY  — orchestrator.key
    mcp_ca_cert: str = Field(default="")       # MCP_CA_CERT     — ca.crt

    # ── Observability (Rule 9) ───────────────────────────────────────────────
    # Tracing backend(s): noop | log | recording | langfuse, comma-separated to
    # compose them. Defaults to noop so a deployment that has not opted in pays nothing.
    observability_backend: str = Field(default="noop")
    # Redaction level: none | pii | strict. Empty means "decide from ENVIRONMENT",
    # which is `pii` in production/staging and `none` in development.
    observability_mask: str = Field(default="")

    # Langfuse credentials. The SDK reads these from the environment itself; they are
    # declared here so cross_validate() can refuse to boot a production deployment
    # that believes it is traced but is not.
    langfuse_public_key: str = Field(default="")
    langfuse_secret_key: str = Field(default="")
    langfuse_base_url: str = Field(default="")
    # Self-hosted Langfuse only — consumed by the Langfuse containers, never by this
    # service. Validated here because it lives in the same .env (Rule 8).
    langfuse_encryption_key: str = Field(default="")

    # ── HTTP bind ────────────────────────────────────────────────────────────
    bind_host: str = Field(default="0.0.0.0")  # nosec B104
    bind_port: int = Field(default=8000, ge=1, le=65535)

    @field_validator("analyst_model_tier")
    @classmethod
    def _tier(cls, v: str) -> str:
        if v.lower() not in {"low", "medium", "high"}:
            raise ValueError("ANALYST_MODEL_TIER must be 'low' | 'medium' | 'high'")
        return v.lower()

    @field_validator("hmac_proposal_key")
    @classmethod
    def _hmac_key(cls, v: str) -> str:
        if v and len(v) < 32:
            raise ValueError("HMAC_PROPOSAL_KEY must be ≥ 32 characters when set")
        return v

    @field_validator("mcp_shared_secret")
    @classmethod
    def _mcp_secret(cls, v: str) -> str:
        if v and len(v) < 32:
            raise ValueError("MCP_SHARED_SECRET must be ≥ 32 characters when set")
        return v

    @field_validator("mcp_claims_url", "mcp_policies_url", "mcp_users_url", "mcp_documents_url")
    @classmethod
    def _url_required(cls, v: str) -> str:
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("MCP URL must be an http(s) endpoint")
        return v

    def cross_validate(self) -> None:
        """Startup-time checks — stronger than field validators.

        Called explicitly in lifespan so import-time instantiation
        (e.g. for CORS config) never raises.
        """
        _PROD_ENVS = {"production", "prod", "staging"}
        in_prod = self.environment.lower() in _PROD_ENVS

        if not self.hmac_proposal_key or len(self.hmac_proposal_key) < 32:
            raise ValueError("HMAC_PROPOSAL_KEY must be ≥ 32 characters")
        if not self.mcp_shared_secret or len(self.mcp_shared_secret) < 32:
            raise ValueError("MCP_SHARED_SECRET must be ≥ 32 characters")
        if self.hmac_proposal_key == self.mcp_shared_secret:
            raise ValueError("HMAC_PROPOSAL_KEY must differ from MCP_SHARED_SECRET")

        if in_prod:
            if not self.jwt_secret_key or self.jwt_secret_key.startswith("dev-only"):
                raise ValueError("JWT_SECRET_KEY must be set to a strong secret in production")
            if len(self.jwt_secret_key) < 32:
                raise ValueError("JWT_SECRET_KEY must be ≥ 32 characters in production")
            # The /tmp literal here REJECTS upload dirs under /tmp; nothing is written
            # to it. Same rationale as _common/settings.validate_upload_dir.
            if self.claim_upload_dir.startswith("/tmp"):  # nosec B108
                raise ValueError("CLAIM_UPLOAD_DIR must not be /tmp in production — use a persistent volume")
            if "langfuse" in self.observability_backend.lower():
                if not self.langfuse_public_key or not self.langfuse_secret_key:
                    raise ValueError(
                        "OBSERVABILITY_BACKEND includes langfuse but "
                        "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are missing — a "
                        "service must not run believing it is traced when it is not"
                    )
                # Rule 8: the placeholder key is 64 zeros, which would leave every
                # secret in the Langfuse database encrypted with a published value.
                if self.langfuse_encryption_key and set(self.langfuse_encryption_key) == {"0"}:
                    raise ValueError(
                        "LANGFUSE_ENCRYPTION_KEY is all zeros — generate one with "
                        "`openssl rand -hex 32`"
                    )

        for url_field in (self.mcp_claims_url, self.mcp_policies_url, self.mcp_users_url, self.mcp_documents_url):
            if not url_field.startswith(("http://", "https://")):
                raise ValueError("MCP URL must be an http(s) endpoint")

        active_spec = {"low": self.low_model, "medium": self.medium_model, "high": self.high_model}[self.analyst_model_tier]
        if not active_spec:
            raise ValueError(f"{self.analyst_model_tier.upper()}_MODEL is not set")
        provider, _, _ = active_spec.partition(":")
        provider = provider.strip().lower()
        if provider == "anthropic" and (not self.anthropic_api_key or self.anthropic_api_key.startswith("PLACEHOLDER")):
            raise ValueError("active tier uses anthropic but ANTHROPIC_API_KEY is missing/placeholder")
        if provider == "openai" and not self.openai_api_key:
            raise ValueError("active tier uses openai but OPENAI_API_KEY is missing")
        if provider == "gemini" and not self.gemini_api_key:
            raise ValueError("active tier uses gemini but GEMINI_API_KEY is missing")
        if provider == "local" and not self.local_base_url:
            raise ValueError("active tier uses local but LOCAL_BASE_URL is missing")


@lru_cache(maxsize=1)
def get_settings() -> OrchestratorSettings:
    """Return a cached OrchestratorSettings instance.

    Safe to call at import time — does NOT run cross_validate().
    cross_validate() is called once in lifespan at startup.
    """
    return OrchestratorSettings()
