"""Stubs for the analyst integration tests.

The two costly external dependencies are mocked:
- LLMClient -> StubLLM that returns scripted responses (tool_use sequences + final JSON)
- MCPClientPool -> StubPool that records every call and returns canned JSON

This lets us assert: ACL propagation, severity overwrite, prompt injection resistance,
write isolation, timeout/MANUAL_REVIEW escalation — without network or token spend.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

from agents.analyst import Analyst, RunContext


@dataclass
class StubLLM:
    """Plays a scripted list of LLMTurn payloads in order, ignoring inputs."""

    script: list[dict[str, Any]] = field(default_factory=list)
    calls: int = 0
    captured: list[dict[str, Any]] = field(default_factory=list)

    model: str = "stub:analyst"

    async def complete(self, *, system: str, messages: list, tools: list, max_tokens: int = 2048):
        from core.models import LLMTurn
        self.captured.append({"messages": messages, "system_len": len(system), "tools": [t["name"] for t in tools]})
        if self.calls >= len(self.script):
            self.calls += 1
            return LLMTurn(stop_reason="end_turn", content_blocks=[{"type": "text", "text": ""}], input_tokens=0, output_tokens=0)
        spec = self.script[self.calls]
        self.calls += 1
        return LLMTurn(
            stop_reason=spec.get("stop_reason", "end_turn"),
            content_blocks=spec["content_blocks"],
            input_tokens=spec.get("in", 0),
            output_tokens=spec.get("out", 0),
        )


@dataclass
class StubPool:
    """Records every call(domain, tool, args) and returns scripted responses by (domain,tool)."""

    responses: dict[tuple[str, str], Any] = field(default_factory=dict)
    calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    async def call(self, domain: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((domain, tool, dict(arguments)))
        key = (domain, tool)
        if key not in self.responses:
            raise AssertionError(f"unexpected tool call: {domain}/{tool}")
        resp = self.responses[key]
        if isinstance(resp, Exception):
            raise resp
        return resp


class _RuntimeView:
    def __init__(self) -> None:
        self.mcp_claims_url = "http://x"
        self.mcp_policies_url = "http://x"
        self.mcp_users_url = "http://x"
        self.mcp_documents_url = "http://x"
        self.mcp_shared_secret = "x" * 32
        self.hmac_proposal_key = "y" * 32
        self.analyst_max_steps = 5
        self.analyst_timeout_seconds = 30
        self.analyst_proposal_ttl_seconds = 60
        self.llm_model = "stub"
        self.anthropic_api_key = "sk-stub"


@pytest.fixture
def runtime():
    return _RuntimeView()


@pytest.fixture
def llm_stub():
    return StubLLM()


@pytest.fixture
def pool_stub():
    return StubPool()


@pytest.fixture
def analyst(runtime, pool_stub, llm_stub):
    return Analyst(runtime=runtime, pool=pool_stub, llm=llm_stub)


@pytest.fixture
def ctx():
    return RunContext(
        pipeline_id=uuid4(),
        caller_sub="analyst@example.com",
        caller_role="ANALYST",
    )


def tool_use(name: str, tool_id: str = "t1", **input_):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": input_}


def final_json(payload: dict[str, Any]):
    return {"type": "text", "text": "```json\n" + json.dumps(payload) + "\n```"}
