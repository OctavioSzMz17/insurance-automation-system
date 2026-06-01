"""Gemini adapter (google-generativeai SDK).

Translation:
  Native Gemini types: Content(role, parts=[Part(text=...) | Part(function_call=...) |
                                            Part(function_response=...)])
  Tools are FunctionDeclarations grouped in a Tool() bundle.

We accept the same normalized format as the other adapters and translate at the edges.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from ...logging_bridge import get_logger
from ..base import LLMClient, LLMTurn

_logger = get_logger(__name__)


class GeminiClient(LLMClient):
    def __init__(self, *, api_key: str, model: str, max_retries: int = 3) -> None:
        import google.generativeai as genai  # local import — optional dep
        genai.configure(api_key=api_key)
        self._genai = genai
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
        # Gemini's Python SDK is sync; we offload to the default thread executor.
        gen_model = self._genai.GenerativeModel(
            model_name=self.model,
            system_instruction=system,
            tools=[{
                "function_declarations": [
                    {"name": t["name"], "description": t.get("description", ""), "parameters": t["input_schema"]}
                    for t in tools
                ]
            }] if tools else None,
        )
        contents = [self._encode_msg(m) for m in messages]

        loop = asyncio.get_running_loop()
        attempt = 0
        while True:
            try:
                response = await loop.run_in_executor(
                    None,
                    lambda: gen_model.generate_content(
                        contents,
                        generation_config={"max_output_tokens": max_tokens},
                    ),
                )
                break
            except Exception as exc:
                msg = str(exc).lower()
                transient = any(s in msg for s in ("429", "500", "502", "503", "504", "timeout"))
                if transient and attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                    attempt += 1
                    continue
                _logger.error("gemini call failed", extra={"event": "llm.fail"})
                raise RuntimeError("llm provider error") from exc

        blocks: list[dict[str, Any]] = []
        candidate = response.candidates[0] if response.candidates else None
        if candidate is None:
            return LLMTurn(stop_reason="end_turn", content_blocks=[], input_tokens=0, output_tokens=0)
        for part in candidate.content.parts:
            text = getattr(part, "text", None)
            if text:
                blocks.append({"type": "text", "text": text})
                continue
            fn = getattr(part, "function_call", None)
            if fn and fn.name:
                args = {k: v for k, v in fn.args.items()} if fn.args else {}
                blocks.append({"type": "tool_use", "id": fn.name, "name": fn.name, "input": args})

        usage = getattr(response, "usage_metadata", None)
        return LLMTurn(
            stop_reason=str(getattr(candidate, "finish_reason", "end_turn")),
            content_blocks=blocks,
            input_tokens=getattr(usage, "prompt_token_count", 0) if usage else 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) if usage else 0,
        )

    def _encode_msg(self, msg: dict[str, Any]) -> dict[str, Any]:
        role = "model" if msg["role"] == "assistant" else "user"
        parts = []
        for b in msg.get("content", []):
            if b["type"] == "text":
                parts.append({"text": b["text"]})
            elif b["type"] == "tool_use":
                parts.append({"function_call": {"name": b["name"], "args": b.get("input", {})}})
            elif b["type"] == "tool_result":
                payload = "\n".join(p.get("text", "") for p in b.get("content", []))
                try:
                    payload_obj = json.loads(payload)
                except (json.JSONDecodeError, ValueError):
                    payload_obj = {"result": payload}
                parts.append({"function_response": {"name": b.get("tool_use_id", "tool"), "response": payload_obj}})
        return {"role": role, "parts": parts}
