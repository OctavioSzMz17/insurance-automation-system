"""MCP client pool — one persistent SSE session per server, shared across tool calls (Rule 7).

Why a pool:
- Rule 7 forbids opening a new HTTP+SSE connection per tool call (pool exhaustion, latency).
- We initialise four sessions in app lifespan startup and tear them down in shutdown.
- Tool callers acquire the existing session by domain name; they never construct one.

Error contract:
- The MCP servers return JSON with a `kind` field ('business' | 'internal').
- 'business' → BusinessError surfaced as-is (the caller decides how to react).
- 'internal' → MCPClientError(public='upstream_internal_error') — raw detail never leaks.

Reconnection:
- If the session drops (network blip, MCP restart), the next acquire reconnects with
  exponential backoff (1, 2, 4, 8, 16s; max 30s). After 5 consecutive failures, the
  caller gets MCPClientError so the pipeline can mark MANUAL_REVIEW rather than hang.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client

from packages.common.exceptions import BusinessError, MCPClientError

from .config_runtime import OrchestratorRuntimeView
from .logging_bridge import get_logger
from .tls import McpHttpClientFactory, make_mtls_factory

_logger = get_logger(__name__)

_DOMAINS = ("claims", "policies", "users", "documents")
_BACKOFF_SEQ = (1, 2, 4, 8, 16, 30)


@dataclass
class _SessionEntry:
    domain: str
    url: str
    secret: str
    session: ClientSession | None = None
    stack: AsyncExitStack | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class MCPClientPool:
    """Owns the four persistent SSE sessions. Lifecycle managed by the app lifespan."""

    def __init__(self, runtime: OrchestratorRuntimeView) -> None:
        self._entries: dict[str, _SessionEntry] = {
            "claims":    _SessionEntry("claims",    runtime.mcp_claims_url,    runtime.mcp_shared_secret),
            "policies":  _SessionEntry("policies",  runtime.mcp_policies_url,  runtime.mcp_shared_secret),
            "users":     _SessionEntry("users",     runtime.mcp_users_url,     runtime.mcp_shared_secret),
            "documents": _SessionEntry("documents", runtime.mcp_documents_url, runtime.mcp_shared_secret),
        }
        # Build mTLS factory once — None when TLS is not configured (dev default).
        self._mtls_factory: McpHttpClientFactory | None = make_mtls_factory(
            client_cert=runtime.mcp_client_cert,
            client_key=runtime.mcp_client_key,
            ca_cert=runtime.mcp_ca_cert,
        )
        if self._mtls_factory is not None:
            _logger.info("mcp pool: mTLS enabled", extra={"event": "mcp.pool.mtls_enabled"})
        self._started = False

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Open all four sessions. Failures are logged and the affected domain is left
        in a reconnect state — start() does NOT raise on partial failure, because in
        development a domain may be offline temporarily and we still want the others up."""
        for domain in _DOMAINS:
            try:
                await self._open(domain)
            except Exception:
                _logger.exception(
                    "mcp pool: initial connect failed",
                    extra={"event": "mcp.pool.connect_fail", "domain": domain},
                )
        self._started = True

    async def stop(self) -> None:
        for entry in self._entries.values():
            await self._close(entry)
        self._started = False

    # ── tool call ────────────────────────────────────────────────────────────

    async def call(self, domain: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        entry = self._entries.get(domain)
        if entry is None:
            raise MCPClientError(public=f"unknown mcp domain: {domain}")
        # Lazy / reconnect-on-demand. The lock serialises reconnection attempts per domain
        # so a burst of callers does not stampede the broken endpoint.
        if entry.session is None:
            async with entry.lock:
                if entry.session is None:
                    await self._open_with_backoff(entry)
        assert entry.session is not None  # noqa: S101 — guarded above
        try:
            result = await entry.session.call_tool(tool, arguments)
        except Exception as exc:  # transport-level failure
            _logger.warning(
                "mcp call failed; will reconnect on next use",
                extra={"event": "mcp.call.fail", "domain": domain, "tool": tool, "err": repr(exc)},
            )
            await self._close(entry)
            raise MCPClientError(public="upstream transport error") from exc

        return _coerce_tool_result(domain, tool, result)

    # ── internal ─────────────────────────────────────────────────────────────

    async def _open(self, domain: str) -> None:
        entry = self._entries[domain]
        await self._close(entry)
        stack = AsyncExitStack()
        sse_kwargs: dict = {"url": f"{entry.url}/sse", "headers": {"X-MCP-Secret": entry.secret}}
        if self._mtls_factory is not None:
            sse_kwargs["httpx_client_factory"] = self._mtls_factory
        try:
            read, write = await stack.enter_async_context(
                sse_client(**sse_kwargs)
            )
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except Exception:
            await stack.aclose()
            raise
        entry.session = session
        entry.stack = stack
        _logger.info("mcp session opened", extra={"event": "mcp.pool.open", "domain": domain})

    async def _open_with_backoff(self, entry: _SessionEntry) -> None:
        for delay in _BACKOFF_SEQ:
            try:
                await self._open(entry.domain)
                return
            except Exception:
                _logger.warning(
                    "mcp reconnect attempt failed",
                    extra={"event": "mcp.pool.reconnect_fail", "domain": entry.domain, "delay": delay},
                )
                await asyncio.sleep(delay)
        raise MCPClientError(public=f"mcp {entry.domain} unreachable")

    async def _close(self, entry: _SessionEntry) -> None:
        stack = entry.stack
        entry.session = None
        entry.stack = None
        if stack is not None:
            try:
                await stack.aclose()
            except Exception:
                _logger.exception(
                    "mcp session close raised",
                    extra={"event": "mcp.pool.close_fail", "domain": entry.domain},
                )


def _coerce_tool_result(domain: str, tool: str, result: Any) -> dict[str, Any]:
    """Convert the SDK's call_tool result into a plain dict, applying the kind contract."""
    # The SDK returns a CallToolResult with .content list of TextContent
    contents = getattr(result, "content", None) or []
    if not contents:
        raise MCPClientError(public="empty tool response")
    first = contents[0]
    text = getattr(first, "text", None)
    if text is None:
        raise MCPClientError(public="non-text tool response")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MCPClientError(public="malformed tool response") from exc

    kind = payload.get("kind")
    if kind == "business":
        # Surface the message as a BusinessError so the agent can react (e.g. forbidden).
        raise BusinessError(payload.get("error", "business error"))
    if kind == "internal":
        _logger.error(
            "mcp returned internal error",
            extra={"event": "mcp.tool.internal", "domain": domain, "tool": tool},
        )
        raise MCPClientError(public="upstream_internal_error")
    return payload
