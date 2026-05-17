"""MCP Router — C2.1

Single dispatch point for every MCP tool invocation made by the
orchestrator. Replaces ad-hoc `_get_claim_by_id(...)` direct calls
scattered across route handlers.

Why it exists:
    1. Centralised RBAC for write-restricted tools (Rule 3).
    2. Centralised retry + backoff so we don't hammer Neon / LLM APIs
       when something transient fails.
    3. Transport abstraction — today calls are in-process imports;
       in production this is the seam we'll swap for SSE clients
       (Phase C SSE work).
    4. Single tool registry — one place to discover what the
       orchestrator can call.

Agents still use their LangChain @tool wrappers (core/tools/*.py) so
the LLM ecosystem keeps working unchanged. This router serves the
orchestrator route handlers directly.

Rules applied:
    Rule 3  — write-restricted tools refuse callers below AUDITOR role
    Rule 7  — caller role flows from the JWT principal (C1.4)
    Rule 9  — structured logging on every call + retry
    Rule 11 — type hints throughout
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import HTTPException, Request, status

# Make project-local imports work when run directly
_ORCH_ROOT     = Path(__file__).resolve().parents[1]
_PROJECT_ROOT  = _ORCH_ROOT.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_ORCH_ROOT))

from auth.roles         import Role, has_role_or_higher       # noqa: E402
from core.tools._loader import load_mcp_tool                  # noqa: E402

logger = logging.getLogger(__name__)


# ── Tool metadata ─────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolInfo:
    """Registered MCP tool — its identity + reference + RBAC flag."""
    mcp:              str                    # e.g. "claims"
    tool:             str                    # e.g. "update_claim_status"
    func:             Callable[..., Awaitable[Any]]
    write_restricted: bool                   # True → requires AUDITOR+


# Exceptions that should NEVER be retried — caller / business errors.
# Identified by class name so we don't need a cross-module import for
# ClaimStateConflictError (defined inside the claims MCP package).
_NON_RETRY_EXCEPTIONS: tuple[type[Exception], ...] = (
    ValueError,
    PermissionError,
)
_NON_RETRY_CLASS_NAMES: frozenset[str] = frozenset({"ClaimStateConflictError"})


# ── The router ────────────────────────────────────────────────────

class MCPRouter:
    """Routes every MCP call the orchestrator makes.

    Attributes:
        max_retries:    Extra attempts after the initial failure.
        base_delay_s:   Initial backoff in seconds; doubles each retry
                        (100ms → 200ms → 400ms by default).
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
        logger.info(f"MCPRouter ready | tools={len(self._tools)}")

    # ── registration ──────────────────────────────────────────────

    def _register_builtin_tools(self) -> None:
        """Wire every MCP tool we know about today."""
        # legacy-provider — read-only
        self.register(ToolInfo(
            "legacy-provider", "get_user_by_id",
            load_mcp_tool("legacy-provider", "get_user_by_id"),
            write_restricted=False,
        ))
        self.register(ToolInfo(
            "legacy-provider", "get_policies_by_user_id",
            load_mcp_tool("legacy-provider", "get_policies_by_user_id"),
            write_restricted=False,
        ))

        # documents — read-only
        self.register(ToolInfo(
            "documents", "extract_claim_from_pdf",
            load_mcp_tool("documents", "extract_claim_from_pdf"),
            write_restricted=False,
        ))

        # claims — mixed
        self.register(ToolInfo(
            "claims", "get_claim_by_id",
            load_mcp_tool("claims", "get_claim_by_id"),
            write_restricted=False,
        ))
        self.register(ToolInfo(
            "claims", "create_claim",
            load_mcp_tool("claims", "create_claim"),
            write_restricted=True,
        ))
        self.register(ToolInfo(
            "claims", "update_claim_status",
            load_mcp_tool("claims", "update_claim_status"),
            write_restricted=True,
        ))

    def register(self, info: ToolInfo) -> None:
        key = (info.mcp, info.tool)
        if key in self._tools:
            raise ValueError(f"Tool already registered: {info.mcp}.{info.tool}")
        self._tools[key] = info

    def list_tools(self) -> list[ToolInfo]:
        """Return a snapshot of every registered tool."""
        return list(self._tools.values())

    # ── public dispatch ───────────────────────────────────────────

    async def call(
        self,
        mcp:         str,
        tool:        str,
        caller_role: Role | None = None,
        **kwargs:    Any,
    ) -> Any:
        """Invoke an MCP tool with RBAC + retry/backoff.

        Args:
            mcp:         MCP server name (e.g. "claims").
            tool:        Tool name (e.g. "update_claim_status").
            caller_role: Role of the caller (from JWT). Required for
                         write-restricted tools.
            **kwargs:    Forwarded to the tool function.

        Returns:
            Whatever the tool returns.

        Raises:
            ValueError:      Unknown tool / caller input error.
            PermissionError: Caller role too low for a write-restricted tool.
            Exception:       Tool's own failure after retries.
        """
        info = self._tools.get((mcp, tool))
        if info is None:
            raise ValueError(f"Unknown MCP tool: {mcp}.{tool}")

        if info.write_restricted:
            self._check_write_access(info, caller_role)

        last_exc: Exception | None = None
        attempts = self.max_retries + 1  # initial + retries

        for attempt in range(1, attempts + 1):
            try:
                logger.debug(
                    f"MCPRouter.call | {mcp}.{tool} attempt={attempt}/{attempts}"
                )
                return await info.func(**kwargs)

            except _NON_RETRY_EXCEPTIONS:
                raise  # caller bug → no retry
            except Exception as e:
                if e.__class__.__name__ in _NON_RETRY_CLASS_NAMES:
                    raise  # business conflict → no retry
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
