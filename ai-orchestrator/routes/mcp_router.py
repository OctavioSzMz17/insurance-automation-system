"""MCP Router — Fix 1 (Rule 7 SSE transport) + C2.1

Single dispatch point for every MCP tool invocation made by the
orchestrator. All calls go out-of-process over SSE to the individual
MCP server endpoints — no in-process file imports (eradicated in Fix 1).

Why it exists:
    1. Centralised RBAC for write-restricted tools (Rule 3).
    2. Centralised retry + backoff so we don't hammer Neon / LLM APIs
       when something transient fails.
    3. Transport abstraction — one place to swap URLs or add mTLS.
    4. Single tool registry — one place to discover what the
       orchestrator can call.

Rules applied:
    Rule 3  — write-restricted tools refuse callers below AUDITOR role
    Rule 7  — ALL MCP calls go over SSE; no in-process invocation
    Rule 9  — structured logging on every call + retry
    Rule 11 — type hints throughout
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, status

_ORCH_ROOT     = Path(__file__).resolve().parents[1]
_PROJECT_ROOT  = _ORCH_ROOT.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_ORCH_ROOT))

from auth.roles      import Role, has_role_or_higher   # noqa: E402
from core.mcp_client import call_mcp_tool              # noqa: E402

logger = logging.getLogger(__name__)


# ── Tool metadata ─────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolInfo:
    """Registered MCP tool — its identity and RBAC flag.

    `func` is intentionally absent (Fix 1): the MCPRouter resolves the
    target URL at call time from Settings and dispatches over SSE.
    """
    mcp:              str   # e.g. "claims"
    tool:             str   # e.g. "update_claim_status"
    write_restricted: bool  # True → requires AUDITOR+


# Exceptions that should NEVER be retried — caller / business errors.
_NON_RETRY_EXCEPTIONS: tuple[type[Exception], ...] = (
    ValueError,
    PermissionError,
)
_NON_RETRY_CLASS_NAMES: frozenset[str] = frozenset({"ClaimStateConflictError"})


# ── The router ────────────────────────────────────────────────────

class MCPRouter:
    """Routes every MCP call the orchestrator makes over SSE (Rule 7).

    Attributes:
        max_retries:    Extra attempts after the initial failure.
        base_delay_s:   Initial backoff in seconds; doubles each retry.
    """

    def __init__(
        self,
        max_retries:  int   = 2,
        base_delay_s: float = 0.1,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if base_delay_s < 0:
            raise ValueError("base_delay_s must be >= 0")

        self.max_retries  = max_retries
        self.base_delay_s = base_delay_s
        self._tools: dict[tuple[str, str], ToolInfo] = {}
        self._register_builtin_tools()
        logger.info(f"MCPRouter ready | tools={len(self._tools)} transport=SSE")

    # ── registration ──────────────────────────────────────────────

    def _register_builtin_tools(self) -> None:
        """Register every MCP tool with its RBAC flag (no func binding)."""
        # legacy-provider — read-only
        self.register(ToolInfo("legacy-provider", "get_user_by_id",          False))
        self.register(ToolInfo("legacy-provider", "get_policies_by_user_id", False))

        # documents — read-only
        self.register(ToolInfo("documents", "extract_claim_from_pdf", False))

        # claims — mixed
        self.register(ToolInfo("claims", "get_claim_by_id",     False))
        self.register(ToolInfo("claims", "list_claims",         False))
        self.register(ToolInfo("claims", "create_claim",        True))
        self.register(ToolInfo("claims", "update_claim_status", True))
        self.register(ToolInfo("claims", "append_reasoning",    True))

    def register(self, info: ToolInfo) -> None:
        key = (info.mcp, info.tool)
        if key in self._tools:
            raise ValueError(f"Tool already registered: {info.mcp}.{info.tool}")
        self._tools[key] = info

    def list_tools(self) -> list[ToolInfo]:
        """Return a snapshot of every registered tool."""
        return list(self._tools.values())

    # ── URL resolution ─────────────────────────────────────────────

    @staticmethod
    def _mcp_url(mcp_name: str) -> str:
        """Resolve the SSE endpoint URL for a given MCP server name."""
        from config import get_settings
        settings = get_settings()
        mapping: dict[str, str] = {
            "legacy-provider": settings.legacy_mcp_url,
            "users":           settings.users_mcp_url,
            "policies":        settings.policies_mcp_url,
            "claims":          settings.claims_mcp_url,
            "documents":       settings.documents_mcp_url,
        }
        url = mapping.get(mcp_name)
        if not url:
            raise ValueError(
                f"No SSE URL configured for MCP server '{mcp_name}'. "
                f"Set the corresponding MCP_*_URL in .env."
            )
        return url

    # ── public dispatch ───────────────────────────────────────────

    async def call(
        self,
        mcp:         str,
        tool:        str,
        caller_role: Role | None = None,
        *,
        internal:    bool = False,
        **kwargs:    Any,
    ) -> Any:
        """Invoke an MCP tool over SSE with RBAC + retry/backoff.

        Args:
            mcp:         MCP server name (e.g. "claims").
            tool:        Tool name (e.g. "update_claim_status").
            caller_role: Role of the caller (from JWT). Required for
                         write-restricted tools unless internal=True.
            internal:    When True, bypass RBAC — only for orchestrator-
                         initiated writes (e.g. inserting AWAITING_INPUT
                         after the agent returns its TriageProposal).
                         Never set from user-facing code paths.
            **kwargs:    Forwarded as the tool's arguments dict.

        Returns:
            Parsed result from the MCP tool.

        Raises:
            ValueError:      Unknown tool or caller input error.
            PermissionError: Caller role too low for a write-restricted tool.
            RuntimeError:    Network/protocol failure after retries.
        """
        info = self._tools.get((mcp, tool))
        if info is None:
            raise ValueError(f"Unknown MCP tool: {mcp}.{tool}")

        if info.write_restricted and not internal:
            self._check_write_access(info, caller_role)

        url = self._mcp_url(mcp)
        last_exc: Exception | None = None
        attempts = self.max_retries + 1

        for attempt in range(1, attempts + 1):
            try:
                logger.debug(
                    f"MCPRouter.call | {mcp}.{tool} attempt={attempt}/{attempts} url={url}"
                )
                return await call_mcp_tool(url, tool, kwargs)

            except _NON_RETRY_EXCEPTIONS:
                raise  # caller/business error → no retry
            except Exception as e:
                if e.__class__.__name__ in _NON_RETRY_CLASS_NAMES:
                    raise
                last_exc = e
                if attempt >= attempts:
                    break

                delay = self.base_delay_s * (2 ** (attempt - 1))
                logger.warning(
                    f"MCPRouter.call retry | {mcp}.{tool} "
                    f"attempt={attempt} err={e!s} backoff={delay:.2f}s"
                )
                await asyncio.sleep(delay)

        assert last_exc is not None
        raise last_exc

    # ── helpers ───────────────────────────────────────────────────

    @staticmethod
    def _check_write_access(info: ToolInfo, caller_role: Role | None) -> None:
        if caller_role is None:
            raise PermissionError(
                f"Tool {info.mcp}.{info.tool} is write-restricted "
                f"and requires a caller_role"
            )
        if not has_role_or_higher(caller_role, Role.AUDITOR):
            raise PermissionError(
                f"Tool {info.mcp}.{info.tool} is write-restricted; "
                f"requires AUDITOR role or higher (got {caller_role.value})"
            )


# ── FastAPI dependency ────────────────────────────────────────────

def get_mcp_router(request: Request) -> MCPRouter:
    """Return the MCPRouter singleton attached to app.state at startup."""
    router = getattr(request.app.state, "mcp_router", None)
    if router is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MCP router not initialised",
        )
    return router
