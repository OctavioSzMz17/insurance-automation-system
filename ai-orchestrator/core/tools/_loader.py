"""In-process loader for MCP tool functions.

The MCP servers are not pip-installable packages (their parent dirs
contain hyphens), and naming them `tools/` collides with this very
package (`core.tools`). So we load the MCP tool modules directly by
file path using importlib.util, bypassing the regular import system.

Phase C C1.3 will replace this loader with real SSE clients.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _load_module(unique_name: str, file_path: Path) -> types.ModuleType:
    """Load a Python module from a file path under a unique sys.modules name."""
    if not file_path.exists():
        raise FileNotFoundError(f"MCP tool module not found: {file_path}")

    spec = importlib.util.spec_from_file_location(unique_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load spec for {file_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = module
    spec.loader.exec_module(module)
    return module


def load_mcp_tool(mcp_server: str, tool_name: str) -> Any:
    """Return the async tool function `tool_name` from `mcp_server`.

    Args:
        mcp_server: Folder name under mcp-servers/ (e.g. "legacy-provider").
        tool_name:  Python file stem under that server's tools/ folder.

    Returns:
        The async function with the same name as the file.
    """
    file_path = _PROJECT_ROOT / "mcp-servers" / mcp_server / "tools" / f"{tool_name}.py"
    unique_name = f"_mcp_{mcp_server.replace('-', '_')}_{tool_name}"
    module = _load_module(unique_name, file_path)
    return getattr(module, tool_name)
