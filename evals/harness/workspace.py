"""Where generated fixtures live.

Default is `evals/.artifacts/uploads` inside the repo (gitignored) so a developer can
open the exact PDF a case failed on. CI overrides it with a temp directory.
"""
from __future__ import annotations

import os
from pathlib import Path

from evals import REPO_ROOT

_ENV_VAR = "EVALS_UPLOAD_DIR"


def default_upload_dir() -> Path:
    override = os.environ.get(_ENV_VAR)
    if override:
        return Path(override).resolve()
    return (REPO_ROOT / "evals" / ".artifacts" / "uploads").resolve()


def report_dir() -> Path:
    return (REPO_ROOT / "evals" / ".artifacts").resolve()
