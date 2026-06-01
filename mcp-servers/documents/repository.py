"""PDF read operations. All filesystem access goes through `storage.safe_pdf_path`."""
from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from packages.common.exceptions import ValidationFailure

from .storage import assert_pdf_readable, safe_pdf_path

_CHUNK = 64 * 1024
_MAX_TEXT_CHARS = 200_000  # cap on extracted text; longer PDFs are truncated with a marker


def compute_sha256(upload_dir: Path, pipeline_id: UUID) -> dict[str, str]:
    path = safe_pdf_path(upload_dir, pipeline_id)
    assert_pdf_readable(path)
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return {"pipeline_id": str(pipeline_id), "sha256": h.hexdigest()}


def extract_text(upload_dir: Path, pipeline_id: UUID) -> dict[str, object]:
    path = safe_pdf_path(upload_dir, pipeline_id)
    assert_pdf_readable(path)
    try:
        reader = PdfReader(str(path))
    except PdfReadError as exc:
        raise ValidationFailure("pdf is malformed") from exc
    pages: list[str] = []
    total = 0
    truncated = False
    for page in reader.pages:
        text = page.extract_text() or ""
        if total + len(text) > _MAX_TEXT_CHARS:
            text = text[: _MAX_TEXT_CHARS - total]
            truncated = True
        pages.append(text)
        total += len(text)
        if truncated:
            break
    return {
        "pipeline_id": str(pipeline_id),
        "page_count": len(reader.pages),
        "text": "\n\n".join(pages),
        "truncated": truncated,
    }


def parse_pdf(upload_dir: Path, pipeline_id: UUID) -> dict[str, object]:
    """Combine sha256 + text extraction into a single tool response."""
    sha = compute_sha256(upload_dir, pipeline_id)
    body = extract_text(upload_dir, pipeline_id)
    return {**body, "sha256": sha["sha256"]}
