"""Bridge to the async Redis client kept in mcp-servers/_common/redis_client.

The orchestrator and the MCPs share the same singleton pattern for Redis — one client per
process, lifespan-managed. This module re-exports the helper so orchestrator code can import
it without reaching into mcp-servers/.
"""
from __future__ import annotations

import sys
from pathlib import Path

# mcp-servers/_common lives in a sibling tree; the runtime PYTHONPATH includes it via the
# Docker entrypoint and the tests' conftest. Mirror that here so app.py works in both.
_MCP_ROOT = Path(__file__).resolve().parents[2] / "mcp-servers"
if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))

from _common.redis_client import dispose_client as dispose_redis  # noqa: E402,F401
from _common.redis_client import get_client as get_redis  # noqa: E402,F401
