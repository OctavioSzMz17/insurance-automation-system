"""Re-exposes the structured logger from _common so orchestrator code uses one logger.

Importing across the dash-named `mcp-servers` dir requires PYTHONPATH = mcp-servers in
the runtime (the Dockerfile sets it; tests set it in conftest).
"""
from __future__ import annotations

from _common.logging import configure, get_logger  # noqa: F401
