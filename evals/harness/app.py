"""Minimal stand-ins for the pieces of the ASGI app the route handlers reach for.

The suite calls the **real** handlers in `routes/agent_router.py` rather than
re-implementing the orchestration, because a reimplementation drifts and then the evals
start certifying a copy of the system instead of the system. The handlers only ever touch
`request.app.state`, so a small attribute bag is enough to drive them, and the FastAPI
dependencies (`require_role`) are bypassed by passing the resolved principal directly —
authentication itself is covered by the unit tests in `ai-orchestrator/tests/unit`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from packages.common.exceptions import ForbiddenError, NotFoundError

from evals.harness.fake_mcp import Phase, RecordingMCPPool


class ScopedPool:
    """A view over one `RecordingMCPPool` that tags its calls with a pipeline phase.

    The agents and the route handler hold *different* references to the pool, which is
    what makes phase attribution exact: anything recorded through the `agent` view was
    issued from inside an agent's graph, so a write seen there is a containment breach
    by construction rather than by heuristic.
    """

    def __init__(self, base: RecordingMCPPool, phase: Phase) -> None:
        self._base = base
        self._phase = phase

    async def call(self, domain: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._base.phase = self._phase
        return await self._base.call(domain, tool, arguments)


class InMemoryHITL:
    """Mirrors the surface of `core.hitl.HITLStore` used by the route handlers.

    Ownership semantics are copied deliberately, including the ADMIN bypass, so the
    anti-IDOR behaviour of /proposal and /confirm is exercised rather than assumed.
    """

    def __init__(self) -> None:
        self.proposals: dict[str, dict[str, Any]] = {}
        self.decisions: dict[str, dict[str, Any]] = {}
        self.consumed: set[str] = set()

    async def put_proposal(self, *, proposal_id: str, owner_sub: str, payload: dict[str, Any]) -> None:
        self.proposals[proposal_id] = json.loads(json.dumps(payload, default=str))

    async def get_proposal(self, proposal_id: str, *, caller_sub: str, caller_role: str) -> dict[str, Any]:
        payload = self.proposals.get(proposal_id)
        if payload is None:
            raise NotFoundError("proposal not found or expired")
        self._assert_owner(payload.get("owner_sub"), caller_sub=caller_sub, caller_role=caller_role)
        return payload

    async def list_proposals_for(self, caller_sub: str) -> list[str]:
        return sorted(k for k, v in self.proposals.items() if v.get("owner_sub") == caller_sub)

    async def put_decision(self, *, decision_id: str, owner_sub: str, payload: dict[str, Any]) -> None:
        self.decisions[decision_id] = json.loads(json.dumps(payload, default=str))

    async def get_decision(self, decision_id: str, *, caller_sub: str, caller_role: str) -> dict[str, Any]:
        payload = self.decisions.get(decision_id)
        if payload is None:
            raise NotFoundError("decision not found or expired")
        self._assert_owner(payload.get("owner_sub"), caller_sub=caller_sub, caller_role=caller_role)
        return payload

    async def list_decisions_for(self, caller_sub: str) -> list[str]:
        return sorted(k for k, v in self.decisions.items() if v.get("owner_sub") == caller_sub)

    async def mark_consumed(self, decision_id: str) -> bool:
        if decision_id in self.consumed:
            return False
        self.consumed.add(decision_id)
        return True

    @staticmethod
    def _assert_owner(stored_owner: str | None, *, caller_sub: str, caller_role: str) -> None:
        if caller_role.upper() == "ADMIN":
            return
        if not stored_owner or stored_owner != caller_sub:
            raise ForbiddenError("forbidden")


@dataclass
class RuntimeView:
    """The narrow config protocol `Analyst` / `Auditor` / `MutationHandler` consume."""

    hmac_proposal_key: str = "evals-hmac-proposal-key-at-least-32-chars"
    mcp_shared_secret: str = "evals-mcp-shared-secret-at-least-32-chars"
    mcp_claims_url: str = "http://claims.invalid"
    mcp_policies_url: str = "http://policies.invalid"
    mcp_users_url: str = "http://users.invalid"
    mcp_documents_url: str = "http://documents.invalid"
    mcp_client_cert: str = ""
    mcp_client_key: str = ""
    mcp_ca_cert: str = ""
    analyst_max_steps: int = 8
    analyst_timeout_seconds: int = 60
    analyst_proposal_ttl_seconds: int = 300
    llm_model: str = "harness"
    anthropic_api_key: str = "not-used-by-the-harness"
    claim_upload_dir: str = ""


def build_request(state_attrs: dict[str, Any]) -> Any:
    """A `request`-shaped object exposing only `request.app.state`."""
    state = SimpleNamespace(**state_attrs)
    app = SimpleNamespace(state=state)
    return SimpleNamespace(app=app)
