"""Thin async HTTP client for the Langfuse public API — Phase D5.1.

We deliberately bypass the official `langfuse` SDK here. Reasons:

1. The SDK's read APIs are awkward to mock and couple us to its
   internal models. Raw HTTP + a small response dict is easier to
   reason about, version, and test.
2. Read traffic is rare (only when an adjuster opens "Ver trace") —
   no performance benefit from the SDK's batching.
3. The interface stays narrow: two methods, returns plain dicts.

The class is safe to instantiate without credentials (every method
raises `LangfuseUnavailable` explicitly instead of half-failing) so
the calling route can return a clean 503/404 to the UI.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class LangfuseUnavailable(RuntimeError):
    """Raised when the Langfuse backend can't be reached or auth'd."""


class LangfuseClient:
    """Tiny GET-only client for the trace + sessions endpoints we need."""

    def __init__(
        self,
        *,
        host:       str,
        public_key: str,
        secret_key: str,
        timeout_s:  float = 8.0,
    ) -> None:
        if not (host and public_key and secret_key):
            raise LangfuseUnavailable(
                "LangfuseClient requires host + public_key + secret_key"
            )
        self._host    = host.rstrip("/")
        self._timeout = timeout_s
        # Langfuse Public API uses HTTP basic auth: public_key:secret_key
        token = base64.b64encode(
            f"{public_key}:{secret_key}".encode("utf-8")
        ).decode("ascii")
        self._headers = {
            "Authorization": f"Basic {token}",
            "Accept":        "application/json",
        }

    # ── public methods ───────────────────────────────────────────

    async def list_traces_by_session(
        self,
        session_id: str,
        *,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Return up to `limit` traces whose sessionId matches.

        Most recent first. Returns an empty list when none match (NOT
        an exception — callers usually want to surface "no trace yet"
        gracefully).
        """
        params = {"sessionId": session_id, "limit": str(limit)}
        data = await self._get("/api/public/traces", params=params)
        return list(data.get("data") or [])

    async def get_trace(self, trace_id: str) -> dict[str, Any]:
        """Return a single trace WITH its observations array.

        Raises `LangfuseUnavailable` on 404 or transport error. The
        endpoint we proxy will translate that to an HTTP 404.
        """
        return await self._get(f"/api/public/traces/{trace_id}")

    # ── internal ─────────────────────────────────────────────────

    async def _get(
        self,
        path: str,
        *,
        params: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        url = f"{self._host}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, headers=self._headers, params=params)
        except httpx.HTTPError as e:
            logger.warning(f"LangfuseClient GET {path} transport error: {e}")
            raise LangfuseUnavailable(f"Could not reach Langfuse at {url}: {e}") from e

        if resp.status_code == 404:
            raise LangfuseUnavailable(f"Langfuse returned 404 for {path}")
        if resp.status_code >= 400:
            logger.warning(
                f"LangfuseClient GET {path} returned {resp.status_code}: "
                f"{resp.text[:200]}"
            )
            raise LangfuseUnavailable(
                f"Langfuse returned HTTP {resp.status_code} for {path}"
            )

        try:
            return resp.json()
        except ValueError as e:
            raise LangfuseUnavailable(f"Langfuse returned non-JSON: {e}") from e


# ── Factory ──────────────────────────────────────────────────────

def get_langfuse_client_from_settings() -> Optional[LangfuseClient]:
    """Build a client from the orchestrator's Settings — None if disabled."""
    try:
        from config import get_settings  # type: ignore
    except ImportError:
        return None
    settings = get_settings()
    if not settings.langfuse_enabled:
        return None
    try:
        return LangfuseClient(
            host=settings.langfuse_host,
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
        )
    except LangfuseUnavailable:
        return None
