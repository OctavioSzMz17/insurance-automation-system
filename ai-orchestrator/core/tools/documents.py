"""Typed wrappers for the documents MCP."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from ._spec import mcp_tool


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "pipeline_id": {"type": "string"},
            "caller_sub": {"type": "string"},
            "caller_role": {"type": "string"},
        },
        "required": ["pipeline_id", "caller_sub", "caller_role"],
        "additionalProperties": False,
    }


@mcp_tool(
    domain="documents",
    mcp_tool="parse_pdf",
    description="Parse a PDF inside CLAIM_UPLOAD_DIR: returns text, page_count, sha256.",
    input_schema=_schema(),
)
async def parse_pdf(pool: Any, *, pipeline_id: UUID | str, caller_sub: str, caller_role: str) -> dict[str, Any]:
    return await pool.call("documents", "parse_pdf", {
        "pipeline_id": str(pipeline_id), "caller_sub": caller_sub, "caller_role": caller_role,
    })


@mcp_tool(
    domain="documents",
    mcp_tool="compute_sha256",
    description="Compute SHA-256 of the PDF for {pipeline_id} inside CLAIM_UPLOAD_DIR.",
    input_schema=_schema(),
)
async def compute_sha256(pool: Any, *, pipeline_id: UUID | str, caller_sub: str, caller_role: str) -> dict[str, Any]:
    return await pool.call("documents", "compute_sha256", {
        "pipeline_id": str(pipeline_id), "caller_sub": caller_sub, "caller_role": caller_role,
    })


@mcp_tool(
    domain="documents",
    mcp_tool="extract_text",
    description="Extract plain text + page count from the PDF. Truncates very large files.",
    input_schema=_schema(),
)
async def extract_text(pool: Any, *, pipeline_id: UUID | str, caller_sub: str, caller_role: str) -> dict[str, Any]:
    return await pool.call("documents", "extract_text", {
        "pipeline_id": str(pipeline_id), "caller_sub": caller_sub, "caller_role": caller_role,
    })
