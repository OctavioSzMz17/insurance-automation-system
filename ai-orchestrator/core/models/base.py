"""Provider-neutral LLM contract.

Every provider adapter implements the LLMClient protocol. The agent only knows about
this surface — adding a new provider means adding a file in `providers/`, registering
its scheme in `registry.py`, and never touching the agent or the tests.

Normalized message/tool shape (independent of provider):
    messages: list[dict]   where each dict is one of
        {"role": "user",      "content": [TextBlock | ToolResultBlock]}
        {"role": "assistant", "content": [TextBlock | ToolUseBlock]}

Block shapes (also normalized):
    TextBlock        = {"type": "text", "text": str}
    ToolUseBlock     = {"type": "tool_use", "id": str, "name": str, "input": dict}
    ToolResultBlock  = {"type": "tool_result", "tool_use_id": str,
                        "content": [TextBlock], "is_error": bool (optional)}

Each provider adapter translates ↔ its native format internally.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class LLMTurn:
    stop_reason: str | None
    content_blocks: list[dict[str, Any]]
    input_tokens: int
    output_tokens: int


class LLMClient(Protocol):
    """Async LLM client. One call per ReAct step."""

    model: str

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 2048,
    ) -> LLMTurn: ...
