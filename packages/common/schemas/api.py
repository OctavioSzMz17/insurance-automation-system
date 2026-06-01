"""HTTP API schemas shared by orchestrator routes (auth + health probes)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class TokenRequest(BaseModel):
    sub: str = Field(min_length=1, max_length=255)
    role: str = Field(min_length=1, max_length=32)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    expires_in: int


class HealthResponse(BaseModel):
    status: str = "ok"
    environment: str


class ReadinessResponse(BaseModel):
    ready: bool
    checks: dict[str, bool]
    details: dict[str, str] = {}


class UploadResponse(BaseModel):
    pipeline_id: str
    file_size: int
    sha256: str
    status: str = "uploaded"
