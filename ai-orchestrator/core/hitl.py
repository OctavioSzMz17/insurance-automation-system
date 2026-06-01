"""HITL coordination store, backed by Redis (Rule 14).

Stores signed `ClaimProposal` (analyst output) and `ClaimDecision` (auditor output) with
TTL=300s. The human-in-the-loop `/confirm` endpoint fetches these by id, verifies the HMAC
signature, and only then invokes the mutation handler.

Key namespace:
    hitl:proposal:{proposal_id}          → JSON of ClaimProposal (with TTL)
    hitl:proposal:owner:{caller_sub}     → SET of proposal_ids owned by this sub (anti-IDOR list)
    hitl:decision:{decision_id}          → JSON of ClaimDecision (with TTL)
    hitl:decision:owner:{caller_sub}     → SET of decision_ids owned by this sub
    hitl:consumed:{decision_id}          → '1' (TTL=900s, longer than the proposal/decision TTL)
                                            — anti-replay: a decision can only be approved once.
    hitl:tracking                        → sorted set: member=proposal_id, score=expiry_timestamp
    hitl:tracking:data:{proposal_id}     → JSON of claim fields needed to create MANUAL_REVIEW
                                            TTL = 2× proposal TTL (survives proposal expiry by design)

All listing is via SMEMBERS (bounded) — never KEYS *.
"""
from __future__ import annotations

import json
import time
from typing import Any, Final

from redis.asyncio import Redis

from packages.common.exceptions import ForbiddenError, NotFoundError

from .logging_bridge import get_logger

_logger = get_logger(__name__)

_DEFAULT_TTL: Final[int] = 300
_CONSUMED_TTL: Final[int] = 900
_TRACKING_SET: Final[str] = "hitl:tracking"
_TRACKING_FIELDS: Final[tuple[str, ...]] = (
    "policy_id", "pdf_sha256", "reported_damage_amount", "severity",
)


def _proposal_key(proposal_id: str) -> str:
    return f"hitl:proposal:{proposal_id}"


def _decision_key(decision_id: str) -> str:
    return f"hitl:decision:{decision_id}"


def _proposals_owner_set(caller_sub: str) -> str:
    return f"hitl:proposal:owner:{caller_sub}"


def _decisions_owner_set(caller_sub: str) -> str:
    return f"hitl:decision:owner:{caller_sub}"


def _consumed_key(decision_id: str) -> str:
    return f"hitl:consumed:{decision_id}"


def _tracking_data_key(proposal_id: str) -> str:
    return f"hitl:tracking:data:{proposal_id}"


class HITLStore:
    """Thin async wrapper around Redis for HITL state."""

    def __init__(self, client: Redis, *, default_ttl: int = _DEFAULT_TTL) -> None:
        self._r = client
        self._ttl = default_ttl

    # ── proposals ───────────────────────────────────────────────────────────

    async def put_proposal(self, *, proposal_id: str, owner_sub: str, payload: dict[str, Any]) -> None:
        pipe = self._r.pipeline()
        pipe.set(_proposal_key(proposal_id), json.dumps(payload, default=str), ex=self._ttl)
        pipe.sadd(_proposals_owner_set(owner_sub), proposal_id)
        pipe.expire(_proposals_owner_set(owner_sub), self._ttl)

        triage = payload.get("triage") or {}
        if all(triage.get(f) for f in _TRACKING_FIELDS):
            tracking_data = json.dumps({
                "submitted_by_sub": owner_sub,
                "policy_id": str(triage["policy_id"]),
                "pdf_sha256": triage["pdf_sha256"],
                "reported_damage_amount": str(triage["reported_damage_amount"]),
                "severity": str(triage["severity"]),
            })
            pipe.set(_tracking_data_key(proposal_id), tracking_data, ex=self._ttl * 2)
            pipe.zadd(_TRACKING_SET, {proposal_id: time.time() + self._ttl})

        await pipe.execute()
        _logger.info(
            "hitl proposal stored",
            extra={"event": "hitl.proposal.put", "proposal_id": proposal_id, "owner_sub": owner_sub},
        )

    async def get_proposal(self, proposal_id: str, *, caller_sub: str, caller_role: str) -> dict[str, Any]:
        raw = await self._r.get(_proposal_key(proposal_id))
        if raw is None:
            raise NotFoundError("proposal not found or expired")
        payload = json.loads(raw)
        self._assert_owner(payload.get("owner_sub"), caller_sub=caller_sub, caller_role=caller_role)
        return payload

    async def list_proposals_for(self, caller_sub: str) -> list[str]:
        members = await self._r.smembers(_proposals_owner_set(caller_sub))
        return sorted(members)

    # ── decisions ───────────────────────────────────────────────────────────

    async def put_decision(self, *, decision_id: str, owner_sub: str, payload: dict[str, Any]) -> None:
        pipe = self._r.pipeline()
        pipe.set(_decision_key(decision_id), json.dumps(payload, default=str), ex=self._ttl)
        pipe.sadd(_decisions_owner_set(owner_sub), decision_id)
        pipe.expire(_decisions_owner_set(owner_sub), self._ttl)
        await pipe.execute()
        _logger.info(
            "hitl decision stored",
            extra={"event": "hitl.decision.put", "decision_id": decision_id, "owner_sub": owner_sub},
        )

    async def get_decision(self, decision_id: str, *, caller_sub: str, caller_role: str) -> dict[str, Any]:
        raw = await self._r.get(_decision_key(decision_id))
        if raw is None:
            raise NotFoundError("decision not found or expired")
        payload = json.loads(raw)
        self._assert_owner(payload.get("owner_sub"), caller_sub=caller_sub, caller_role=caller_role)
        return payload

    async def list_decisions_for(self, caller_sub: str) -> list[str]:
        members = await self._r.smembers(_decisions_owner_set(caller_sub))
        return sorted(members)

    # ── anti-replay ─────────────────────────────────────────────────────────

    async def mark_consumed(self, decision_id: str) -> bool:
        """Atomic: returns True if this is the first time the decision is consumed.

        Rule 6: each approval triggers AT MOST one mutation. SET NX guarantees idempotency
        even under concurrent /confirm requests.
        decision_id == proposal_id by convention, so ZREM uses it directly to clear the
        reaper tracking entry (preventing a redundant MANUAL_REVIEW after human action).
        """
        ok = await self._r.set(_consumed_key(decision_id), "1", ex=_CONSUMED_TTL, nx=True)
        if ok:
            pipe = self._r.pipeline()
            pipe.zrem(_TRACKING_SET, decision_id)
            pipe.delete(_tracking_data_key(decision_id))
            await pipe.execute()
            _logger.info(
                "hitl decision consumed",
                extra={"event": "hitl.decision.consumed", "decision_id": decision_id},
            )
        return bool(ok)

    # ── helpers ─────────────────────────────────────────────────────────────

    def _assert_owner(self, stored_owner: str | None, *, caller_sub: str, caller_role: str) -> None:
        """ADMIN reads anyone's record; ANALYST only its own (anti-IDOR — Rule 7)."""
        if caller_role.upper() == "ADMIN":
            return
        if not stored_owner or stored_owner != caller_sub:
            raise ForbiddenError("forbidden")
