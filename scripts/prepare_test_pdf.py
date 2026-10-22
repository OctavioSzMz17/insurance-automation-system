"""Prepare a user-supplied PDF for manual testing.

Usage:
    python scripts/prepare_test_pdf.py "C:\\path\\to\\my-claim.pdf"

What it does:
    1. Validates the file exists and is a PDF.
    2. Generates a fresh UUID (this is the pipeline_id you'll use in /analyze).
    3. Copies the file to CLAIM_UPLOAD_DIR as {uuid}.pdf  (server-controlled name,
       per documents-MCP path-traversal defense).
    4. Computes SHA-256 (so you can verify dedup behaviour later).
    5. Prints the pipeline_id + suggested curl variables.

The original PDF is NOT moved or modified.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import uuid
from pathlib import Path


def _resolve_upload_dir() -> Path:
    """Read CLAIM_UPLOAD_DIR from .env (manual parse — keeps the script dep-free)."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        print(f"ERROR: .env not found at {env_path}", file=sys.stderr)
        sys.exit(2)
    upload_dir: str | None = None
    with env_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("CLAIM_UPLOAD_DIR="):
                upload_dir = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not upload_dir:
        print("ERROR: CLAIM_UPLOAD_DIR not set in .env", file=sys.stderr)
        sys.exit(2)
    p = Path(upload_dir).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_path", help="Path to your test PDF")
    args = parser.parse_args()

    src = Path(args.pdf_path).expanduser().resolve()
    if not src.exists():
        print(f"ERROR: file not found: {src}", file=sys.stderr)
        return 1
    if src.suffix.lower() != ".pdf":
        print(f"ERROR: not a .pdf file: {src.name}", file=sys.stderr)
        return 1
    if not src.is_file():
        print(f"ERROR: not a regular file: {src}", file=sys.stderr)
        return 1

    upload_dir = _resolve_upload_dir()
    pipeline_id = uuid.uuid4()
    target = upload_dir / f"{pipeline_id}.pdf"

    shutil.copy2(src, target)

    sha = hashlib.sha256()
    with target.open("rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            sha.update(chunk)
    sha_hex = sha.hexdigest()
    size_kb = target.stat().st_size / 1024

    print()
    print("=" * 70)
    print(" PDF READY FOR TESTING")
    print("=" * 70)
    print(f"  source       : {src}")
    print(f"  copied to    : {target}")
    print(f"  size         : {size_kb:.1f} KB")
    print(f"  sha256       : {sha_hex}")
    print()
    print(f"  pipeline_id  : {pipeline_id}")
    print()
    print(" Suggested shell variables (PowerShell):")
    print(f'   $PID = "{pipeline_id}"')
    print()
    print(" Suggested shell variables (bash / git-bash):")
    print(f'   export PID="{pipeline_id}"')
    print()
    print(" Next step: see docs/manual-testing.md for the full /analyze → /audit → /confirm walk-through.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
