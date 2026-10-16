"""Re-export of the canonical structured logger (Rule 9).

The implementation moved to `observability/logging.py`, which is the path Rule 9 names.
This shim stays because every module under `_common` imports `from .logging import
get_logger`, and because the MCP servers should not have to care where it lives.

Requires `observability/` on the PYTHONPATH — the container images get it from
`infra/docker/Dockerfile.mcp-base`.
"""
from __future__ import annotations

from observability.logging import configure, get_logger

__all__ = ["configure", "get_logger"]
