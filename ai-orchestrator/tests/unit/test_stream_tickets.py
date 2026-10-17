"""Unit tests for auth/stream_tickets.py — StreamTicketStore."""
from __future__ import annotations

import time

import pytest

from auth.roles import Role
from auth.stream_tickets import StreamTicket, StreamTicketStore, TicketQuotaExceeded

# ── In-memory backend (no Redis) ──────────────────────────────────────────────


def _store(ttl: int = 30, max_per_sub: int = 5) -> StreamTicketStore:
    return StreamTicketStore(ttl_seconds=ttl, max_per_sub=max_per_sub)


@pytest.mark.asyncio
async def test_mint_returns_string_id():
    store = _store()
    tid = await store.mint(sub="alice", role=Role.ANALYST, request_id="req-1")
    assert isinstance(tid, str)
    assert len(tid) == 36  # UUID4


@pytest.mark.asyncio
async def test_pop_returns_ticket():
    store = _store()
    tid = await store.mint(sub="alice", role=Role.ANALYST, request_id="req-1")
    ticket = await store.pop(tid)
    assert ticket is not None
    assert isinstance(ticket, StreamTicket)
    assert ticket.sub == "alice"
    assert ticket.role == Role.ANALYST
    assert ticket.request_id == "req-1"


@pytest.mark.asyncio
async def test_pop_is_one_time_use():
    """A ticket can only be redeemed once."""
    store = _store()
    tid = await store.mint(sub="alice", role=Role.ANALYST, request_id="req-1")
    first = await store.pop(tid)
    second = await store.pop(tid)
    assert first is not None
    assert second is None


@pytest.mark.asyncio
async def test_pop_unknown_ticket_returns_none():
    store = _store()
    result = await store.pop("00000000-0000-0000-0000-000000000000")
    assert result is None


@pytest.mark.asyncio
async def test_pop_empty_id_returns_none():
    store = _store()
    assert await store.pop("") is None


@pytest.mark.asyncio
async def test_ticket_has_future_expires_at():
    store = _store(ttl=30)
    tid = await store.mint(sub="bob", role=Role.AUDITOR, request_id="req-2")
    ticket = await store.pop(tid)
    assert ticket is not None
    assert ticket.expires_at > time.time()
    assert ticket.expires_at <= time.time() + 31


@pytest.mark.asyncio
async def test_quota_exceeded_raises():
    store = _store(max_per_sub=2)
    await store.mint(sub="carol", role=Role.ANALYST, request_id="req-a")
    await store.mint(sub="carol", role=Role.ANALYST, request_id="req-b")
    with pytest.raises(TicketQuotaExceeded) as exc_info:
        await store.mint(sub="carol", role=Role.ANALYST, request_id="req-c")
    assert exc_info.value.sub == "carol"
    assert exc_info.value.cap == 2


@pytest.mark.asyncio
async def test_quota_is_per_sub():
    """Quota is per-subject — different subs are independent."""
    store = _store(max_per_sub=1)
    await store.mint(sub="alice", role=Role.ANALYST, request_id="req-1")
    # alice is at quota, but bob is not
    tid_bob = await store.mint(sub="bob", role=Role.ANALYST, request_id="req-2")
    assert tid_bob is not None


@pytest.mark.asyncio
async def test_consumed_ticket_frees_quota():
    """Popping a ticket allows minting another within quota."""
    store = _store(max_per_sub=1)
    tid1 = await store.mint(sub="dave", role=Role.ANALYST, request_id="req-1")
    await store.pop(tid1)   # consume it → slot freed
    # Now quota is back to 0/1
    tid2 = await store.mint(sub="dave", role=Role.ANALYST, request_id="req-2")
    assert tid2 is not None


@pytest.mark.asyncio
async def test_different_subs_get_different_tickets():
    store = _store()
    t1 = await store.mint(sub="alice", role=Role.ANALYST, request_id="r1")
    t2 = await store.mint(sub="bob", role=Role.AUDITOR, request_id="r2")
    assert t1 != t2
    ticket1 = await store.pop(t1)
    ticket2 = await store.pop(t2)
    assert ticket1 is not None and ticket1.sub == "alice"
    assert ticket2 is not None and ticket2.sub == "bob"


@pytest.mark.asyncio
async def test_constructor_validates_params():
    with pytest.raises(ValueError):
        StreamTicketStore(maxsize=0)
    with pytest.raises(ValueError):
        StreamTicketStore(ttl_seconds=0)
    with pytest.raises(ValueError):
        StreamTicketStore(max_per_sub=0)


@pytest.mark.asyncio
async def test_mint_validates_empty_sub():
    store = _store()
    with pytest.raises(ValueError):
        await store.mint(sub="", role=Role.ANALYST, request_id="req-1")
