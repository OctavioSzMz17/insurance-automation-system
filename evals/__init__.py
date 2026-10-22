"""Adversarial evaluation suite for the claims pipeline.

Importing this package bootstraps the interpreter paths the monorepo needs (the repo
root, `ai-orchestrator/` and the dash-named `mcp-servers/`) and pins the few environment
variables the harness depends on.

Deliberately NOT loaded here: `.env`. The suite must produce identical results on a
developer laptop and in CI, so it never reads deployment configuration and never needs a
provider API key — the model is injected by the harness. Live mode (`runner.py --live`)
is the one exception and reads its key explicitly.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ORCH_ROOT = REPO_ROOT / "ai-orchestrator"
MCP_ROOT = REPO_ROOT / "mcp-servers"

for _path in (REPO_ROOT, ORCH_ROOT, MCP_ROOT):
    _as_str = str(_path)
    if _as_str not in sys.path:
        sys.path.insert(0, _as_str)

# The intake write at /analyze is a legitimate, human-gated mutation, so the write gate
# is open. What the suite asserts is that no write is *attempted* from inside an agent
# loop — see `harness/invariants.py`.
os.environ.setdefault("MCP_WRITE_ENABLED", "1")

__all__ = ["REPO_ROOT", "ORCH_ROOT", "MCP_ROOT"]
