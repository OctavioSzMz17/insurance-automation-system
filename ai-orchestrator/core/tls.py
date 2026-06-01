"""mTLS factory for outbound MCP connections.

The MCP SDK's sse_client() accepts an `httpx_client_factory` kwarg — a callable
with signature (headers, timeout, auth) → httpx.AsyncClient. This module builds
that factory when cert paths are configured, injecting an SSL context that:
  - verifies the MCP server cert against the CA
  - presents the orchestrator client cert so the MCP can verify the caller's identity

When any cert path is absent (default in dev without TLS) the factory is None and
sse_client falls back to its default (plain HTTP).
"""
from __future__ import annotations

import ssl
from pathlib import Path
from typing import Callable

import httpx

# Type alias matching mcp.shared._httpx_utils.McpHttpClientFactory
McpHttpClientFactory = Callable[
    [dict[str, str] | None, httpx.Timeout | None, httpx.Auth | None],
    httpx.AsyncClient,
]


def make_mtls_factory(
    *,
    client_cert: str,
    client_key: str,
    ca_cert: str,
) -> McpHttpClientFactory | None:
    """Return an httpx_client_factory for mTLS, or None if any path is empty.

    Args:
        client_cert: Path to the orchestrator's client certificate (PEM).
        client_key:  Path to the orchestrator's client private key (PEM).
        ca_cert:     Path to the CA certificate used to verify MCP server certs (PEM).

    Returns:
        A factory callable if all three paths are non-empty and the files exist,
        otherwise None (caller gets plain-HTTP behaviour).
    """
    if not (client_cert and client_key and ca_cert):
        return None

    # Validate paths eagerly at startup so misconfiguration surfaces immediately.
    for label, path_str in (("MCP_CLIENT_CERT", client_cert), ("MCP_CLIENT_KEY", client_key), ("MCP_CA_CERT", ca_cert)):
        if not Path(path_str).is_file():
            raise FileNotFoundError(f"{label} path does not exist: {path_str!r}")

    ctx = ssl.create_default_context(cafile=ca_cert)
    ctx.load_cert_chain(certfile=client_cert, keyfile=client_key)
    # Require valid server cert — never disable verification.
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = True

    def _factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        kwargs: dict = {"verify": ctx}
        if headers:
            kwargs["headers"] = headers
        if timeout is not None:
            kwargs["timeout"] = timeout
        if auth is not None:
            kwargs["auth"] = auth
        return httpx.AsyncClient(**kwargs)

    return _factory
