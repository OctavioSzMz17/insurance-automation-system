"""POST /submit-upload — multipart PDF intake (Phase 4 F4.2).

Accepts a PDF from a client, validates it, writes it to CLAIM_UPLOAD_DIR
as {pipeline_id}.pdf, and returns the server-generated pipeline_id.

The documents MCP discovers the file by UUID so no MCP call is needed
here — the shared volume mount makes it immediately visible.

Security controls:
  - JWT required (ANALYST role or higher).
  - Size capped at MAX_UPLOAD_BYTES (default 10 MB) before writing to disk.
  - PDF magic bytes (%PDF) checked in addition to content-type.
  - pipeline_id is a server-generated UUID4 — never derived from caller input.
  - Destination path is constructed from the UUID and verified to stay
    inside CLAIM_UPLOAD_DIR (path-traversal hardening).
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from uuid import uuid4

from config import get_settings
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status

from auth import Role, UserPrincipal, require_role
from observability import get_tracer
from packages.common.schemas.api import UploadResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["upload"])

_PDF_MAGIC = b"%PDF"
_ALLOWED_CONTENT_TYPES = {"application/pdf", "application/octet-stream"}


@router.post(
    "/submit-upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a claim PDF and receive a pipeline_id",
)
async def submit_upload(
    file: UploadFile,
    request: Request,
    user: UserPrincipal = Depends(require_role(Role.ANALYST)),
) -> UploadResponse:
    """Receive a PDF, validate it, persist it, and return a pipeline_id.

    The caller uses the returned pipeline_id in POST /analyze.
    """
    settings = get_settings()

    # ── 1. Content-type check (first line of defence — not sole guard) ────────
    content_type = (file.content_type or "").lower().split(";")[0].strip()
    if content_type not in _ALLOWED_CONTENT_TYPES and content_type != "":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF files are accepted (application/pdf).",
        )

    # ── 2. Read with hard size limit — prevents OOM on huge uploads ───────────
    max_bytes = settings.max_upload_bytes
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the maximum allowed size of {max_bytes // (1024 * 1024)} MB.",
        )
    if len(content) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file.")

    # ── 3. Magic bytes check — content-type can be spoofed, bytes cannot ─────
    if not content.startswith(_PDF_MAGIC):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="File does not appear to be a valid PDF (missing %PDF header).",
        )

    # ── 4. Write to CLAIM_UPLOAD_DIR/{pipeline_id}.pdf ────────────────────────
    pipeline_id = uuid4()
    upload_dir = Path(settings.claim_upload_dir).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)

    dest = (upload_dir / f"{pipeline_id}.pdf").resolve()
    # Path-traversal guard: resolved dest must stay inside upload_dir.
    if not dest.is_relative_to(upload_dir):
        logger.error("Path traversal attempt blocked | dest=%s upload_dir=%s", dest, upload_dir)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file path.")

    dest.write_bytes(content)

    sha256 = hashlib.sha256(content).hexdigest()
    async with get_tracer().trace(
        "claim.upload",
        session_id=str(pipeline_id),
        pipeline_id=str(pipeline_id),
        operator_sub=user.sub,
    ) as span:
        span.set_attributes(bytes=len(content), sha256_12=sha256[:12],
                            content_type=content_type or "unset")
    logger.info(
        "upload accepted | pipeline_id=%s size=%d sha256=%s sub=%s",
        pipeline_id, len(content), sha256[:16], user.sub,
    )

    return UploadResponse(
        pipeline_id=str(pipeline_id),
        file_size=len(content),
        sha256=sha256,
    )
