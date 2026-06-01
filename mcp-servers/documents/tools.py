"""Documents MCP tool handlers (read-only, path-traversal hardened)."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from _common.server_wiring import ToolSpec
from _common.settings import MCPSettings

from . import repository as repo


class _PdfRequest(BaseModel):
    """Common input: a validated UUID and the caller identity."""

    model_config = ConfigDict(extra="forbid")
    pipeline_id: UUID
    caller_sub: str = Field(min_length=1, max_length=255)
    caller_role: str = Field(min_length=1, max_length=32)


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()


def build_tools(settings: MCPSettings) -> list[ToolSpec]:
    upload_dir: Path = settings.validate_upload_dir()

    async def compute_sha256(args: dict[str, Any]) -> dict[str, Any]:
        parsed = _PdfRequest.model_validate(args)
        return repo.compute_sha256(upload_dir, parsed.pipeline_id)

    async def extract_text(args: dict[str, Any]) -> dict[str, Any]:
        parsed = _PdfRequest.model_validate(args)
        return repo.extract_text(upload_dir, parsed.pipeline_id)

    async def parse_pdf(args: dict[str, Any]) -> dict[str, Any]:
        parsed = _PdfRequest.model_validate(args)
        return repo.parse_pdf(upload_dir, parsed.pipeline_id)

    return [
        ToolSpec(
            name="compute_sha256",
            description="Compute SHA-256 of the PDF for {pipeline_id} inside CLAIM_UPLOAD_DIR.",
            input_schema=_schema(_PdfRequest),
            handler=compute_sha256,
        ),
        ToolSpec(
            name="extract_text",
            description="Extract plain text + page count from the PDF. Truncates very large files.",
            input_schema=_schema(_PdfRequest),
            handler=extract_text,
        ),
        ToolSpec(
            name="parse_pdf",
            description="One-shot: returns text, page_count, and sha256 of the PDF.",
            input_schema=_schema(_PdfRequest),
            handler=parse_pdf,
        ),
    ]
