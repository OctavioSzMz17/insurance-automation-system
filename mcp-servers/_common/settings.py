"""Per-MCP settings loaded from environment. Fail-hard at startup on weak secrets (Rule 8)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProtectedEnv = Literal["production", "prod", "staging"]
_PROTECTED: frozenset[str] = frozenset({"production", "prod", "staging"})


class MCPSettings(BaseSettings):
    """Common settings every MCP server reads.

    Each MCP supplies its own `database_url` (least-privilege credential per domain — Rule 3).
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = Field(default="development")
    mcp_domain: str = Field(min_length=1)
    mcp_shared_secret: str = Field(default="")
    database_url: str = Field(default="")
    redis_url: str = Field(default="")
    log_level: str = Field(default="INFO")
    # Containers must bind on all interfaces; the network policy / ingress is the boundary.
    bind_host: str = Field(default="0.0.0.0")  # nosec B104
    bind_port: int = Field(default=8000, ge=1, le=65535)
    claim_upload_dir: str = Field(default="")
    # mTLS server cert paths (F4.7). All three must be set to enable TLS.
    # Generate with: bash scripts/certs/gen_dev_certs.sh
    mcp_tls_cert: str = Field(default="")   # MCP_TLS_CERT — path to server cert (PEM)
    mcp_tls_key: str = Field(default="")    # MCP_TLS_KEY  — path to server private key (PEM)
    mcp_ca_cert: str = Field(default="")    # MCP_CA_CERT  — CA cert for client verification (PEM)

    @field_validator("mcp_shared_secret")
    @classmethod
    def _validate_secret(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("MCP_SHARED_SECRET must be non-empty at startup")
        if len(v) < 32:
            raise ValueError("MCP_SHARED_SECRET must be at least 32 characters")
        return v

    @field_validator("database_url")
    @classmethod
    def _validate_dsn(cls, v: str) -> str:
        if not v.startswith(("postgresql://", "postgresql+asyncpg://")):
            raise ValueError("DATABASE_URL must be a PostgreSQL DSN")
        return v

    def is_protected_env(self) -> bool:
        return self.environment.lower() in _PROTECTED

    def validate_upload_dir(self) -> Path:
        """Documents MCP calls this. /tmp is forbidden in protected envs (Rule 8)."""
        if not self.claim_upload_dir:
            raise ValueError("CLAIM_UPLOAD_DIR is required for the documents MCP")
        p = Path(self.claim_upload_dir).resolve()
        if self.is_protected_env():
            # The /tmp literal here is used to REJECT upload dirs under /tmp, never to write.
            tmp = Path("/tmp").resolve()  # nosec B108
            if p == tmp or tmp in p.parents:
                raise ValueError("CLAIM_UPLOAD_DIR must not live under /tmp in protected env")
        return p
