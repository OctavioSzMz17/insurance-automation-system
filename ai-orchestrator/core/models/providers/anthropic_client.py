"""Anthropic adapter. Native shape ≈ our normalized shape — minimal translation."""
from __future__ import annotations

import asyncio
from typing import Any

from anthropic import APIStatusError, AsyncAnthropic

from ...logging_bridge import get_logger
from ..base import LLMClient, LLMTurn

_logger = get_logger(__name__)


class AnthropicClient(LLMClient):
    def __init__(self, *, api_key: str, model: str, max_retries: int = 3) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self.model = model
        self._max_retries = max_retries

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 2048,
    ) -> LLMTurn:
        attempt = 0
        while True:
            try:
                response = await self._client.messages.create(
                    model=self.model, system=system, messages=messages, tools=tools, max_tokens=max_tokens,
                )
                break
            except APIStatusError as exc:
                if exc.status_code in (429, 500, 502, 503, 504) and attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                    attempt += 1
                    continue
                _logger.error("anthropic call failed", extra={"event": "llm.fail", "status": exc.status_code})
                raise RuntimeError("llm provider error") from exc

        blocks: list[dict[str, Any]] = []
        for c in response.content:
            kind = getattr(c, "type", None)
            if kind == "text":
                blocks.append({"type": "text", "text": c.text})
            elif kind == "tool_use":
                blocks.append({"type": "tool_use", "id": c.id, "name": c.name, "input": dict(c.input or {})})

        usage = getattr(response, "usage", None)
        return LLMTurn(
            stop_reason=response.stop_reason,
            content_blocks=blocks,
            input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
        )
