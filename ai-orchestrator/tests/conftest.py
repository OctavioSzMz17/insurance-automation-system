"""Shared sys.path bootstrap for ai-orchestrator tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ORCH_ROOT = _REPO_ROOT / "ai-orchestrator"
_MCP_ROOT = _REPO_ROOT / "mcp-servers"
for p in (_REPO_ROOT, _ORCH_ROOT, _MCP_ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

load_dotenv(_REPO_ROOT / ".env")
# Tests must never use a real API key
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-fake-key-not-real")
