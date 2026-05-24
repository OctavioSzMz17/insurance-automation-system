"""LangChain @tool wrappers around the MCP server functions.

Rule 6 (HITL) enforcement: create_claim is NOT in any agent tool bundle.
DB persistence is handled exclusively by the orchestrator FastAPI handler
after the agent produces its TriageProposal — no autonomous mutations.

Exports:
    DOCUMENTS_TOOLS  — PDF extraction (read-only)           [analyst + auditor]
    LEGACY_TOOLS     — users + policies lookup (read-only)  [analyst + auditor]
    REASONING_TOOLS  — deterministic coverage rules engine  [auditor only]

Pre-built tool bundles:
    READ_ONLY_TOOLS  = documents + legacy       (for analyst_agent)
    AUDITOR_TOOLS    = READ_ONLY_TOOLS + reasoning engine (for auditor_agent)
"""

from .documents import DOCUMENTS_TOOLS
from .legacy import LEGACY_TOOLS
from .reasoning import REASONING_TOOLS

READ_ONLY_TOOLS = [*DOCUMENTS_TOOLS, *LEGACY_TOOLS]
# Auditor gets read tools + the deterministic rules engine.
# create_claim is deliberately absent — Rule 6 HITL gate.
AUDITOR_TOOLS   = [*READ_ONLY_TOOLS, *REASONING_TOOLS]

# Back-compat alias for the analyst_agent — same as READ_ONLY_TOOLS
ALL_TOOLS = READ_ONLY_TOOLS

__all__ = [
    "DOCUMENTS_TOOLS",
    "LEGACY_TOOLS",
    "REASONING_TOOLS",
    "READ_ONLY_TOOLS",
    "AUDITOR_TOOLS",
    "ALL_TOOLS",
]
