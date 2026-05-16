"""LangChain @tool wrappers around the MCP server functions.

Each wrapper is what an LLM agent sees as a callable tool. Under the hood
they delegate to the real MCP tool function. For Phase B we call the MCP
tool functions directly in-process — Phase C C1.3 will swap these to SSE
clients calling the running MCP servers over the network.

Exports:
    DOCUMENTS_TOOLS  — PDF extraction (read-only)            [analyst + auditor]
    LEGACY_TOOLS     — users + policies lookup (read-only)   [analyst + auditor]
    CLAIMS_TOOLS     — claim writes (@write_restricted)      [auditor only]

Pre-built tool bundles:
    READ_ONLY_TOOLS  = documents + legacy        (for analyst_agent)
    AUDITOR_TOOLS    = READ_ONLY_TOOLS + claims  (for auditor_agent)

The analyst_agent must NEVER receive CLAIMS_TOOLS — its role is READ-ONLY
(Rule 4). Bundling them separately makes that boundary explicit.
"""

from .documents import DOCUMENTS_TOOLS
from .legacy import LEGACY_TOOLS
from .claims import CLAIMS_TOOLS
from .reasoning import REASONING_TOOLS

READ_ONLY_TOOLS = [*DOCUMENTS_TOOLS, *LEGACY_TOOLS]
# Auditor gets read tools + the deterministic rules engine + write tool
AUDITOR_TOOLS   = [*READ_ONLY_TOOLS, *REASONING_TOOLS, *CLAIMS_TOOLS]

# Back-compat alias for the analyst_agent — same as READ_ONLY_TOOLS
ALL_TOOLS = READ_ONLY_TOOLS

__all__ = [
    "DOCUMENTS_TOOLS",
    "LEGACY_TOOLS",
    "CLAIMS_TOOLS",
    "REASONING_TOOLS",
    "READ_ONLY_TOOLS",
    "AUDITOR_TOOLS",
    "ALL_TOOLS",
]
