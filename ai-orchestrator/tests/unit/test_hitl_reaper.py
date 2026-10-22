"""Unit tests for workers/hitl_reaper.py and the hitl:tracking additions to core/hitl.py."""
from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from core.hitl import _TRACKING_SET, HITLStore, _tracking_data_key
from workers.hitl_reaper import _process_expired, run_reaper

# ── Fake Redis ────────────────────────────────────────────────────────────────

class _FakeRedis:
    """Minimal in-memory Redis fake supporting the operations used by HITLStore + reaper."""

    def __init__(self) -> None:
        self._strings: dict[str, tuple[str, float | None]] = {}  # key → (value, expires_at)
        self._sets: dict[str, set[str]] = {}
        self._zsets: dict[str, dict[str, float]] = {}  # key → {member: score}
        self._pipeline_log: list[tuple] = []

    # -- string ops --

    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False) -> bool:
        exp = time.time() + ex if ex else None
        if nx and key in self._strings:
            v, exp_at = self._strings[key]
            if exp_at is None or exp_at > time.time():
                return False
        self._strings[key] = (value, exp)
        return True

    async def get(self, key: str) -> bytes | None:
        entry = self._strings.get(key)
        if entry is None:
            return None
        val, exp_at = entry
        if exp_at is not None and exp_at <= time.time():
            del self._strings[key]
            return None
        return val.encode()

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._strings:
                del self._strings[k]
                count += 1
        return count

    # -- set ops --

    async def sadd(self, key: str, *members: str) -> int:
        s = self._sets.setdefault(key, set())
        added = sum(1 for m in members if m not in s)
        s.update(members)
        return added

    async def smembers(self, key: str) -> set[str]:
        return set(self._sets.get(key, set()))

    async def expire(self, key: str, ttl: int) -> int:
        return 1

    # -- sorted set ops --

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        z = self._zsets.setdefault(key, {})
        added = sum(1 for m in mapping if m not in z)
        z.update(mapping)
        return added

    async def zrangebyscore(self, key: str, min_score: str | float, max_score: str | float) -> list[bytes]:
        z = self._zsets.get(key, {})
        lo = float("-inf") if min_score == "-inf" else float(min_score)
        hi = float("inf") if max_score == "+inf" else float(max_score)
        return [m.encode() for m, s in z.items() if lo <= s <= hi]

    async def zrem(self, key: str, *members: str) -> int:
        z = self._zsets.get(key, {})
        removed = 0
        for m in members:
            if m in z:
                del z[m]
                removed += 1
        return removed

    # -- pipeline --

    def pipeline(self) -> "_FakePipeline":
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, redis: _FakeRedis) -> None:
        self._r = redis
        self._ops: list = []

    def set(self, key, value, ex=None, nx=False):
        self._ops.append(("set", key, value, ex, nx))
        return self

    def sadd(self, key, *members):
        self._ops.append(("sadd", key, members))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))
        return self

    def zadd(self, key, mapping):
        self._ops.append(("zadd", key, mapping))
        return self

    def zrem(self, key, *members):
        self._ops.append(("zrem", key, members))
        return self

    def delete(self, *keys):
        self._ops.append(("delete", keys))
        return self

    async def execute(self) -> list:
        results = []
        for op in self._ops:
            if op[0] == "set":
                _, key, value, ex, nx = op
                results.append(await self._r.set(key, value, ex=ex, nx=nx))
            elif op[0] == "sadd":
                _, key, members = op
                results.append(await self._r.sadd(key, *members))
            elif op[0] == "expire":
                _, key, ttl = op
                results.append(await self._r.expire(key, ttl))
            elif op[0] == "zadd":
                _, key, mapping = op
                results.append(await self._r.zadd(key, mapping))
            elif op[0] == "zrem":
                _, key, members = op
                results.append(await self._r.zrem(key, *members))
            elif op[0] == "delete":
                _, keys = op
                results.append(await self._r.delete(*keys))
        return results


def _make_store(ttl: int = 30) -> tuple[HITLStore, _FakeRedis]:
    redis = _FakeRedis()
    store = HITLStore(redis, default_ttl=ttl)
    return store, redis


def _triage_payload(policy_id: str = "pol-1") -> dict:
    return {
        "policy_id": policy_id,
        "pdf_sha256": "abc123",
        "reported_damage_amount": "5000.00",
        "severity": "HIGH",
        "pipeline_id": "pipe-1",
        "summary": "damage",
        "evidence_quotes": [],
    }


# ── HITLStore tracking additions ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_proposal_writes_tracking_set():
    store, redis = _make_store(ttl=30)
    payload = {"owner_sub": "alice", "pipeline_id": "p1", "triage": _triage_payload(), "proposal": {}}
    await store.put_proposal(proposal_id="prop-1", owner_sub="alice", payload=payload)

    # hitl:tracking should have 'prop-1' with future score
    members = await redis.zrangebyscore(_TRACKING_SET, "-inf", "+inf")
    assert b"prop-1" in members

    # tracking data key should exist with claim fields
    data_raw = await redis.get(_tracking_data_key("prop-1"))
    assert data_raw is not None
    data = json.loads(data_raw)
    assert data["submitted_by_sub"] == "alice"
    assert data["policy_id"] == "pol-1"
    assert data["pdf_sha256"] == "abc123"
    assert data["severity"] == "HIGH"


@pytest.mark.asyncio
async def test_put_proposal_no_triage_skips_tracking():
    store, redis = _make_store()
    payload = {"owner_sub": "alice", "pipeline_id": "p1", "triage": None, "proposal": {}}
    await store.put_proposal(proposal_id="prop-2", owner_sub="alice", payload=payload)

    members = await redis.zrangebyscore(_TRACKING_SET, "-inf", "+inf")
    assert b"prop-2" not in members


@pytest.mark.asyncio
async def test_put_proposal_partial_triage_skips_tracking():
    store, redis = _make_store()
    triage = {"policy_id": "pol-x"}  # missing pdf_sha256, reported_damage_amount, severity
    payload = {"owner_sub": "alice", "pipeline_id": "p1", "triage": triage, "proposal": {}}
    await store.put_proposal(proposal_id="prop-3", owner_sub="alice", payload=payload)

    members = await redis.zrangebyscore(_TRACKING_SET, "-inf", "+inf")
    assert b"prop-3" not in members


@pytest.mark.asyncio
async def test_mark_consumed_cleans_tracking():
    store, redis = _make_store(ttl=30)
    # Seed a tracking entry manually so we can verify removal
    await redis.zadd(_TRACKING_SET, {"prop-4": time.time() - 10})
    await redis.set(_tracking_data_key("prop-4"), json.dumps({"sub": "alice"}))

    result = await store.mark_consumed("prop-4")
    assert result is True

    # Tracking set should no longer contain prop-4
    members = await redis.zrangebyscore(_TRACKING_SET, "-inf", "+inf")
    assert b"prop-4" not in members

    # Data key should be gone
    assert await redis.get(_tracking_data_key("prop-4")) is None


@pytest.mark.asyncio
async def test_mark_consumed_duplicate_skips_cleanup():
    store, redis = _make_store()
    # First consume sets the key
    await store.mark_consumed("prop-5")
    # Manually re-add tracking entry to verify the second call doesn't touch it
    await redis.zadd(_TRACKING_SET, {"prop-5": time.time() - 5})

    result = await store.mark_consumed("prop-5")  # duplicate
    assert result is False

    # Tracking entry was NOT removed by the duplicate call
    members = await redis.zrangebyscore(_TRACKING_SET, "-inf", "+inf")
    assert b"prop-5" in members


# ── _process_expired ──────────────────────────────────────────────────────────


def _make_pool_mock():
    pool = AsyncMock()
    pool.call = AsyncMock(return_value={"claim": {"id": "new-claim-id"}})
    return pool


@pytest.mark.asyncio
async def test_process_expired_empty_returns_zero():
    redis = _FakeRedis()
    pool = _make_pool_mock()
    count = await _process_expired(redis, pool, time.time())
    assert count == 0


@pytest.mark.asyncio
async def test_process_expired_creates_manual_review():
    redis = _FakeRedis()
    pool = _make_pool_mock()

    proposal_id = "aaaa-expired"
    entry = {
        "submitted_by_sub": "alice",
        "policy_id": "pol-1",
        "pdf_sha256": "sha256abc",
        "reported_damage_amount": "3000.00",
        "severity": "MEDIUM",
    }
    past_score = time.time() - 5
    await redis.zadd(_TRACKING_SET, {proposal_id: past_score})
    await redis.set(f"hitl:tracking:data:{proposal_id}", json.dumps(entry))

    with patch("workers.hitl_reaper.dispatch", new=AsyncMock(return_value={"claim": {}})) as mock_dispatch:
        count = await _process_expired(redis, pool, time.time())

    assert count == 1
    mock_dispatch.assert_awaited_once()
    call_args = mock_dispatch.call_args
    assert call_args.args[1] == "claims__create_claim"
    body = call_args.args[2]
    assert body["status"] == "MANUAL_REVIEW"
    assert body["submitted_by_sub"] == "alice"
    assert body["pdf_sha256"] == "sha256abc"
    assert call_args.kwargs["allow_writes"] is True


@pytest.mark.asyncio
async def test_process_expired_removes_tracking_on_success():
    redis = _FakeRedis()
    pool = _make_pool_mock()

    proposal_id = "bbbb-expired"
    past_score = time.time() - 10
    await redis.zadd(_TRACKING_SET, {proposal_id: past_score})
    await redis.set(f"hitl:tracking:data:{proposal_id}", json.dumps({
        "submitted_by_sub": "bob", "policy_id": "pol-2",
        "pdf_sha256": "sha2", "reported_damage_amount": "1000", "severity": "LOW",
    }))

    with patch("workers.hitl_reaper.dispatch", new=AsyncMock(return_value={})):
        await _process_expired(redis, pool, time.time())

    members = await redis.zrangebyscore(_TRACKING_SET, "-inf", "+inf")
    assert proposal_id.encode() not in members
    assert await redis.get(f"hitl:tracking:data:{proposal_id}") is None


@pytest.mark.asyncio
async def test_process_expired_missing_data_removes_orphan():
    redis = _FakeRedis()
    pool = _make_pool_mock()

    proposal_id = "cccc-orphan"
    await redis.zadd(_TRACKING_SET, {proposal_id: time.time() - 5})
    # data key intentionally absent

    with patch("workers.hitl_reaper.dispatch", new=AsyncMock()) as mock_dispatch:
        count = await _process_expired(redis, pool, time.time())

    assert count == 0
    mock_dispatch.assert_not_awaited()
    # Orphan should be cleaned up from tracking set
    members = await redis.zrangebyscore(_TRACKING_SET, "-inf", "+inf")
    assert proposal_id.encode() not in members


@pytest.mark.asyncio
async def test_process_expired_dispatch_error_keeps_entry():
    redis = _FakeRedis()
    pool = _make_pool_mock()

    proposal_id = "dddd-error"
    await redis.zadd(_TRACKING_SET, {proposal_id: time.time() - 5})
    await redis.set(f"hitl:tracking:data:{proposal_id}", json.dumps({
        "submitted_by_sub": "carol", "policy_id": "pol-3",
        "pdf_sha256": "sha3", "reported_damage_amount": "2000", "severity": "HIGH",
    }))

    with patch("workers.hitl_reaper.dispatch", new=AsyncMock(side_effect=RuntimeError("MCP down"))):
        count = await _process_expired(redis, pool, time.time())

    assert count == 0
    # Entry should remain for retry on next cycle
    members = await redis.zrangebyscore(_TRACKING_SET, "-inf", "+inf")
    assert proposal_id.encode() in members


@pytest.mark.asyncio
async def test_process_expired_future_entries_not_processed():
    redis = _FakeRedis()
    pool = _make_pool_mock()

    future_score = time.time() + 9999
    await redis.zadd(_TRACKING_SET, {"future-prop": future_score})
    await redis.set("hitl:tracking:data:future-prop", json.dumps({
        "submitted_by_sub": "dave", "policy_id": "pol-x",
        "pdf_sha256": "sha-x", "reported_damage_amount": "500", "severity": "LOW",
    }))

    with patch("workers.hitl_reaper.dispatch", new=AsyncMock()) as mock_dispatch:
        count = await _process_expired(redis, pool, time.time())

    assert count == 0
    mock_dispatch.assert_not_awaited()


# ── run_reaper cancellation ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_reaper_stops_on_cancel():
    """run_reaper should exit cleanly when its task is cancelled."""
    import asyncio as _asyncio

    redis = _FakeRedis()
    pool = _make_pool_mock()

    with patch("workers.hitl_reaper._DEFAULT_INTERVAL", 3600):
        task = _asyncio.create_task(run_reaper(redis, pool, interval=3600))
        # Give the event loop a tick to enter the sleep
        await _asyncio.sleep(0)
        task.cancel()
        await _asyncio.wait_for(task, timeout=1.0)

    assert task.done()
    assert not task.exception() if not task.cancelled() else True
