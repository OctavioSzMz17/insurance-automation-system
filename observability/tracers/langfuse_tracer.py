"""Langfuse implementation of the BaseTracer interface — Phase D2.1.

Each call to `callback_handler(...)` returns a fresh
`langfuse.callback.CallbackHandler` scoped to the current request.
LangGraph attaches it to the executor, and Langfuse auto-captures
every LLM call + tool invocation with proper nesting.

We deliberately catch and swallow any Langfuse-side errors:
observability **must never break the product**. Failed shipment is
logged once and the request keeps moving.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from .base_tracer import BaseTracer

logger = logging.getLogger(__name__)


class LangfuseTracer(BaseTracer):
    """Wraps the `langfuse` SDK behind our `BaseTracer` interface.

    The SDK is imported lazily inside `__init__` so the orchestrator
    can still boot when `langfuse` is not installed (e.g. minimal
    test environments). If the import fails or auth is rejected, the
    tracer downgrades to disabled instead of raising.
    """

    def __init__(
        self,
        *,
        host:       str,
        public_key: str,
        secret_key: str,
    ) -> None:
        self._enabled = False
        self._client: Any | None = None
        self._callback_cls: Any | None = None

        try:
            from langfuse import Langfuse                       # noqa: WPS433
            from langfuse.callback import CallbackHandler       # noqa: WPS433
        except ImportError as e:
            logger.warning(
                f"LangfuseTracer disabled — SDK not installed: {e}"
            )
            return

        try:
            self._client = Langfuse(
                host=host,
                public_key=public_key,
                secret_key=secret_key,
                # Flush after every span for low-latency dev visibility.
                # In prod, raise this to batch traces.
                flush_at=1,
                flush_interval=1,
            )
            # Hit the auth endpoint cheaply to fail fast if the keys
            # are wrong / the server is down. Returns True/False, no raise.
            if not self._client.auth_check():
                logger.warning(
                    f"LangfuseTracer disabled — auth_check() failed against {host}. "
                    f"Check LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY."
                )
                return
        except Exception as e:
            logger.warning(
                f"LangfuseTracer disabled — could not initialise client: {e}"
            )
            return

        self._callback_cls = CallbackHandler
        self._host = host
        self._public_key = public_key
        self._secret_key = secret_key
        self._enabled = True
        logger.info(f"LangfuseTracer ready | host={host}")

    # ── BaseTracer surface ───────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    def callback_handler(
        self,
        *,
        trace_name: str,
        user_id:    Optional[str] = None,
        session_id: Optional[str] = None,
        tags:       Optional[list[str]] = None,
        metadata:   Optional[Mapping[str, Any]] = None,
    ) -> Optional[Any]:
        if not self._enabled or self._callback_cls is None:
            return None

        try:
            return self._callback_cls(
                host=self._host,
                public_key=self._public_key,
                secret_key=self._secret_key,
                trace_name=trace_name,
                user_id=user_id,
                session_id=session_id,
                tags=tags or [],
                metadata=dict(metadata or {}),
            )
        except Exception as e:
            # Never break the request because of telemetry.
            logger.warning(
                f"LangfuseTracer.callback_handler failed | err={e}"
            )
            return None

    def flush(self) -> None:
        if not self._enabled or self._client is None:
            return
        try:
            self._client.flush()
        except Exception as e:
            logger.warning(f"LangfuseTracer.flush failed | err={e}")
