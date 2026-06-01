"""SSE stream ticket endpoints (Phase 4 — Rule 7 anti ?token=JWT).

Flow:
  1. Client holds a valid Bearer JWT.
  2. Client calls POST /sse/ticket  →  receives a short-lived ticket_id (TTL 30s).
  3. Client opens  GET /sse?ticket={ticket_id}  without a Bearer token in the URL.
  4. Server pops+validates the ticket atomically, upgrades to SSE stream.

This prevents JWTs from appearing in proxy logs, browser history, or HTTP
referrer headers.

SSE event types emitted:
  connected  — sent immediately after ticket validation.
  heartbeat  — sent every 15 s to keep the connection alive through proxies.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from starlette.responses import StreamingResponse

from auth import Role, UserPrincipal, require_role
from auth.stream_tickets import StreamTicketStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sse", tags=["sse"])


# ── POST /sse/ticket ──────────────────────────────────────────────────────────


@router.post(
    "/ticket",
    status_code=status.HTTP_201_CREATED,
    summary="Mint a one-time SSE stream ticket",
    description=(
        "Returns a short-lived ticket_id (TTL 30 s) that the client uses to "
        "open GET /sse?ticket={ticket_id}. Requires a valid Bearer JWT."
    ),
)
async def mint_ticket(
    request: Request,
    user: UserPrincipal = Depends(require_role(Role.ANALYST)),
) -> dict:
    store: StreamTicketStore | None = getattr(request.app.state, "ticket_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SSE ticket store not initialised.",
        )

    request_id = str(uuid4())
    try:
        ticket_id = await store.mint(sub=user.sub, role=user.role, request_id=request_id)
    except Exception as exc:
        logger.error("Failed to mint SSE ticket | sub=%s err=%s", user.sub, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not mint stream ticket.",
        )

    logger.info("SSE ticket minted | sub=%s request_id=%s", user.sub, request_id)
    return {"ticket_id": ticket_id, "request_id": request_id, "expires_in": store._ttl}


# ── GET /sse?ticket={ticket_id} ───────────────────────────────────────────────


@router.get(
    "",
    summary="Open an SSE stream (no Bearer token in URL)",
    response_class=StreamingResponse,
)
async def sse_stream(
    request: Request,
    ticket: str = Query(..., min_length=1),
) -> StreamingResponse:
    """Validate the one-time ticket and upgrade to an SSE stream."""
    store: StreamTicketStore | None = getattr(request.app.state, "ticket_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SSE ticket store not initialised.",
        )

    stream_ticket = await store.pop(ticket)
    if stream_ticket is None:
        logger.warning("SSE ticket invalid or already used | ticket=%s", ticket[:8])
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="SSE ticket is invalid, expired, or has already been used.",
        )

    if time.time() > stream_ticket.expires_at:
        logger.warning("SSE ticket expired | ticket=%s sub=%s", ticket[:8], stream_ticket.sub)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="SSE ticket has expired.",
        )

    logger.info(
        "SSE connection established | sub=%s request_id=%s",
        stream_ticket.sub, stream_ticket.request_id,
    )

    return StreamingResponse(
        _event_stream(request, stream_ticket.sub, stream_ticket.role.value, stream_ticket.request_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",   # disable nginx response buffering
        },
    )


async def _event_stream(
    request: Request,
    sub: str,
    role: str,
    request_id: str,
    *,
    heartbeat_interval: float = 15.0,
    max_heartbeats: int | None = None,
):
    """Generate SSE events until the client disconnects.

    heartbeat_interval / max_heartbeats are exposed for tests only.
    """
    connected_payload = json.dumps({
        "sub": sub,
        "role": role,
        "request_id": request_id,
    })
    yield f"event: connected\ndata: {connected_payload}\n\n"

    sent = 0
    while max_heartbeats is None or sent < max_heartbeats:
        if await request.is_disconnected():
            logger.debug("SSE client disconnected | sub=%s", sub)
            break
        await asyncio.sleep(heartbeat_interval)
        heartbeat = json.dumps({"ts": int(time.time())})
        yield f"event: heartbeat\ndata: {heartbeat}\n\n"
        sent += 1
