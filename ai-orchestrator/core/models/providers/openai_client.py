"""OpenAI adapter. Also used for `local:` providers via custom base_url.

Translation:
  Native OpenAI request:
      messages: [{role:'system'|'user'|'assistant'|'tool', content/tool_calls/tool_call_id}]
      tools:    [{type:'function', function:{name, description, parameters}}]
  → from our normalized shape.

Tool flow:
  assistant emits tool_calls[*] → we surface as ToolUseBlock(s).
  We feed tool results back as role:'tool' messages tied by tool_call_id.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from openai import APIStatusError, AsyncOpenAI

from ...logging_bridge import get_logger
from ..base import LLMClient, LLMTurn

_logger = get_logger(__name__)

# Roles the OpenAI Chat Completions API accepts. Anything else must be
# normalized before the request, otherwise the API rejects it with a 400.
_OPENAI_ROLES = frozenset({"system", "assistant", "user", "function", "tool", "developer"})


class OpenAIClient(LLMClient):
    def __init__(self, *, api_key: str, model: str, base_url: str | None = None, max_retries: int = 3) -> None:
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)
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
        # Each normalized message may expand into 0..N provider messages
        # (e.g. a user turn carrying several tool_result blocks becomes several
        # role='tool' messages). Flatten, then sanitize roles defensively so no
        # internal/unknown role ever reaches the provider.
        oa_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            oa_messages.extend(self._encode_msg(m))
        oa_messages = [self._sanitize_msg(m) for m in oa_messages]
        oa_tools = [
            {"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                              "parameters": t["input_schema"]}}
            for t in tools
        ]

        attempt = 0
        while True:
            try:
                # `max_completion_tokens` is the current parameter (gpt-4o and
                # gpt-5.x). The legacy `max_tokens` is rejected by gpt-5-era models.
                resp = await self._client.chat.completions.create(
                    model=self.model, messages=oa_messages, tools=oa_tools or None,
                    max_completion_tokens=max_tokens,
                )
                break
            except APIStatusError as exc:
                if exc.status_code in (429, 500, 502, 503, 504) and attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                    attempt += 1
                    continue
                _logger.error("openai call failed", extra={"event": "llm.fail", "status": exc.status_code})
                raise RuntimeError("llm provider error") from exc

        choice = resp.choices[0]
        msg = choice.message
        blocks: list[dict[str, Any]] = []
        if msg.content:
            blocks.append({"type": "text", "text": msg.content})
        for tc in (msg.tool_calls or []):
            try:
                input_dict = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                input_dict = {"_raw": tc.function.arguments}
            blocks.append({"type": "tool_use", "id": tc.id, "name": tc.function.name, "input": input_dict})

        usage = getattr(resp, "usage", None)
        return LLMTurn(
            stop_reason=choice.finish_reason,
            content_blocks=blocks,
            input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
        )

    def _encode_msg(self, msg: dict[str, Any]) -> list[dict[str, Any]]:
        """Translate one normalized message into 0..N OpenAI messages.

        Always returns a list so the caller can flatten uniformly — no magic
        marker roles. A user turn carrying tool_result blocks expands into one
        role='tool' message per result, exactly as the OpenAI API requires.
        """
        role = msg.get("role")
        content = msg.get("content", [])
        # Content can already be a plain string (some providers / cached turns).
        if isinstance(content, str):
            return [{"role": role if role in _OPENAI_ROLES else "user", "content": content}]

        if role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for b in content:
                if b.get("type") == "text":
                    text_parts.append(b.get("text", ""))
                elif b.get("type") == "tool_use":
                    tool_calls.append({
                        "id": b["id"],
                        "type": "function",
                        "function": {"name": b["name"], "arguments": json.dumps(b.get("input", {}))},
                    })
            out: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) or None}
            if tool_calls:
                out["tool_calls"] = tool_calls
            return [out]

        if role == "user":
            # Tool results must each become a standalone role='tool' message keyed
            # by tool_call_id; plain text stays a single user message.
            tool_results = [b for b in content if b.get("type") == "tool_result"]
            if tool_results:
                return [
                    {
                        "role": "tool",
                        "tool_call_id": b["tool_use_id"],
                        "content": "\n".join(p.get("text", "") for p in b.get("content", [])),
                    }
                    for b in tool_results
                ]
            text = "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
            return [{"role": "user", "content": text}]

        # Unknown role: hand off as-is; _sanitize_msg will coerce it safely.
        return [{"role": role, "content": content}]

    @staticmethod
    def _sanitize_msg(msg: dict[str, Any]) -> dict[str, Any]:
        """Guarantee a provider-valid role and string content.

        Any role outside the OpenAI-accepted set is degraded to 'user', and any
        non-string content is JSON-stringified, so a single odd message can never
        500 the whole pipeline with a 400 invalid_request_error.
        """
        role = msg.get("role")
        if role in _OPENAI_ROLES:
            # assistant may legitimately carry content=None alongside tool_calls.
            return msg
        content = msg.get("content")
        if not isinstance(content, str):
            content = json.dumps(content, default=str)
        _logger.warning(
            "coerced unknown LLM message role to 'user'",
            extra={"event": "llm.role_coerced", "role": str(role)},
        )
        return {"role": "user", "content": content}
