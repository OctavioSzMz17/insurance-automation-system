"""Filesystem helpers for the documents MCP — single place where paths are constructed.

If you add a new code path that opens a PDF, route it through `safe_pdf_path()`.
Never read files using a caller-supplied filename or any unvalidated path component.
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

from packages.common.exceptions import NotFoundError, ValidationFailure


def safe_pdf_path(upload_dir: Path, pipeline_id: UUID) -> Path:
    """Return the canonical resolved PDF path for a pipeline_id, or raise.

    The filename is constructed from the validated UUID — never from caller input.
    The resolved path is verified to live INSIDE `upload_dir` even after symlink resolution.
    """
    base = upload_dir.resolve()
    candidate = (base / f"{pipeline_id}.pdf").resolve()
    if not candidate.is_relative_to(base):
        raise ValidationFailure("invalid pdf path")
    return candidate


def assert_pdf_readable(path: Path) -> None:
    """Existence + regular-file + extension checks. Symlinks were already collapsed by resolve()."""
    if not path.exists():
        raise NotFoundError("pdf not found")
    if not path.is_file():
        raise ValidationFailure("not a regular file")
    if path.suffix.lower() != ".pdf":
        raise ValidationFailure("not a pdf")
