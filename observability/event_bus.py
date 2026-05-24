"""In-memory event bus — Phase E1.1.

Lets the orchestrator publish per-step events as Lía runs and a Server-
Sent Events handler stream them to the browser in real time. Single
process, no Redis, no Kafka — fits the current architecture; trivial
to swap later by reimplementing this module.

Topology:

    [agent_router]         [SSE handler]
    publish(rid, ev)  ───▶  subscribe(rid)  ───▶  browser EventSource

Each `request_id` gets its own asyncio.Queue, created lazily on first
publish OR first subscribe (whichever happens first). The queue is
single-consumer: opening the same claim in two browser tabs only
streams to the first to subscribe. That's a deliberate simplification
— fan-out can be added later by switching the value to a list of
queues.

Cleanup:
    - Subscriber finalizer drops the queue when its async generator
      exits (normal completion or client disconnect).
    - Publishers that never get a subscriber leak a queue per orphan
      request. A periodic GC task (see `start_gc` in app lifespan)
      sweeps queues with no activity in `_IDLE_TIMEOUT_S` seconds.

Failure modes:
    - Queue full → drop the event with a WARNING log. Observability
      must never break the product (Rule 6 spirit).
    - No subscriber yet → events queue up to `_QUEUE_MAXSIZE`, then
      drop oldest. The SSE handler typically connects before the
      heavy work starts, so backlog stays small in practice.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Literal, Optional, TypedDict

logger = logging.getLogger(__name__)

# Tunables — keep low for a single dev machine; raise for prod.
_QUEUE_MAXSIZE   = 256
_IDLE_TIMEOUT_S  = 300  # 5 min
_GC_INTERVAL_S   = 60
# Fix #78 — Redis history list TTL.  Must outlive the longest pipeline so
# a late SSE subscriber can replay all events from the list.
_REDIS_HISTORY_TTL = _IDLE_TIMEOUT_S   # 300 s — same as the in-memory idle timeout


# ── Event schema ─────────────────────────────────────────────────

EventType = Literal[
    "pipeline.start",   # POST received, agent_router about to spin up
    "agent.start",      # analyst or auditor began
    "agent.end",        # analyst or auditor finished
    "tool.call",        # an MCP tool was invoked
    "tool.result",      # the MCP tool returned
    "agent.decision",   # auditor produced the final ClaimDecision
    "pipeline.done",    # everything finished successfully
    "pipeline.error",   # something blew up; subscribers should disconnect
]

# `total` is False because not every field applies to every event type.
class StreamEvent(TypedDict, total=False):
    type:       EventType                 # required
    request_id: str                       # required — for client filtering
    timestamp:  str                       # required — ISO-8601 UTC
    stage:      str                       # "analyst" | "auditor" | "system"
    name:       str                       # tool name, agent name, etc.
    data:       dict[str, Any]            # type-specific payload
    latency_ms: int                       # for *.end / tool.result events


def make_event(
    type: EventType,
    request_id: str,
    *,
    stage: Optional[str] = None,
    name:  Optional[str] = None,
    data:  Optional[dict[str, Any]] = None,
    latency_ms: Optional[int] = None,
) -> StreamEvent:
    """Build a well-shaped StreamEvent with ISO timestamp filled in."""
    ev: StreamEvent = {
        "type":       type,
        "request_id": request_id,
        "timestamp":  datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
    }
    if stage      is not None: ev["stage"]      = stage
    if name       is not None: ev["name"]       = name
    if data       is not None: ev["data"]       = data
    if latency_ms is not None: ev["latency_ms"] = latency_ms
    return ev


# ── Bus ──────────────────────────────────────────────────────────

class _BusEntry:
    """A queue + the wall-clock of its last touch (for idle GC)."""
    __slots__ = ("queue", "last_touch")

    def __init__(self) -> None:
        self.queue: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self.last_touch: float = time.monotonic()

    def touch(self) -> None:
        self.last_touch = time.monotonic()


class EventBus:
    """Pub/sub event bus for SSE streaming.

    Backed by in-process asyncio.Queue per request_id (single-replica).
    Fix #78 — when a Redis client is wired via set_redis(), publish()
    additionally pushes events to a Redis List (replay) + Pub/Sub channel
    (live), and subscribe() reads from Redis instead of the local queue.
    This makes the EventBus work correctly across multiple orchestrator
    replicas without requiring Kafka or a message broker.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _BusEntry] = {}
        self._gc_task: Optional[asyncio.Task[None]] = None
        self._redis = None   # redis.asyncio.Redis | None; set via set_redis()

    # ── Redis wiring ─────────────────────────────────────────────

    def set_redis(self, client) -> None:
        """Wire a Redis client for multi-replica Pub/Sub support.

        Fix #78 — call this at app startup (after the Redis client is
        ready) before the first publish/subscribe call.  Safe to call
        with None (disables Redis, falls back to in-memory).

        Args:
            client: redis.asyncio.Redis client, or None.
        """
        self._redis = client
        backend = "redis+pubsub" if client is not None else "in-memory"
        logger.info("EventBus backend configured | %s", backend)

    # ── publish ──────────────────────────────────────────────────

    def publish(self, request_id: str, event: StreamEvent) -> None:
        """Non-blocking put. Never raises; never blocks the caller.

        Always writes to the in-process asyncio.Queue (local subscribers
        on this replica). When Redis is configured, also fires-and-forgets
        a coroutine that pushes to the Redis history list + Pub/Sub channel
        so subscribers on OTHER replicas receive the event.

        Backpressure: if the per-request queue is full, drop the
        OLDEST event (typical of streaming dashboards — recency wins).
        """
        if not request_id:
            return
        entry = self._get_or_create(request_id)
        entry.touch()
        q = entry.queue
        if q.full():
            try:
                q.get_nowait()  # drop oldest
            except asyncio.QueueEmpty:
                pass
            logger.warning(
                "event_bus | queue full for %s; dropped oldest event",
                request_id,
            )
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # extremely unlikely (we just drained one), but be defensive
            logger.warning(
                "event_bus | failed to enqueue event for %s after drain",
                request_id,
            )

        # Fix #78 — fire-and-forget Redis publish (never blocks the caller).
        if self._redis is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self._publish_redis(request_id, event),
                    name=f"event_bus_redis_pub_{request_id[:8]}",
                )
            except RuntimeError:
                pass  # no running loop (sync tests / import time)

    # ── subscribe ────────────────────────────────────────────────

    async def subscribe(
        self,
        request_id: str,
        *,
        keepalive_s: float = 15.0,
        require_existing: bool = True,
    ) -> AsyncIterator[Optional[StreamEvent]]:
        """Async iterator over events for `request_id`.

        Fix #78 — delegates to Redis Pub/Sub backend when available so
        any orchestrator replica can serve this SSE stream.

        Yields:
            - `StreamEvent` for each published event
            - `None` periodically as a keepalive nudge so the SSE
              handler can flush a comment-line to detect dropped
              client connections

        Args:
            request_id:        Pipeline correlation id to subscribe to.
            keepalive_s:       Cadence of `None` yields when the queue
                               is idle.
            require_existing:  Master Patch Part 4 — when True (default),
                               do NOT materialise an empty queue for an
                               unknown request_id. The generator returns
                               immediately without yielding, so the
                               caller's `async for` loop exits cleanly.
                               This closes the connection-hoarding DoS
                               vector where an attacker could open SSE
                               streams against arbitrary request_ids and
                               pin file descriptors + coroutines for
                               minutes at a time waiting for events
                               that will never arrive.

        Terminates when a `pipeline.done` or `pipeline.error` event
        flows through, or when the consumer breaks out of the loop.
        Cleans up the queue on exit.
        """
        # Fix #78 — Redis backend takes priority for multi-replica support.
        if self._redis is not None:
            async for event in self._subscribe_redis(
                request_id, keepalive_s, require_existing
            ):
                yield event
            return

        # ── Original in-memory path ──────────────────────────────

        # Master Patch Part 4 — fail-fast for unknown request_ids.
        if require_existing:
            entry = self._entries.get(request_id)
            if entry is None:
                logger.info(
                    "event_bus.subscribe | unknown request_id=%s require_existing=True; "
                    "closing immediately",
                    request_id,
                )
                return  # async generator exits — caller sees zero events
        else:
            entry = self._get_or_create(request_id)

        q = entry.queue
        try:
            while True:
                entry.touch()
                try:
                    event = await asyncio.wait_for(q.get(), timeout=keepalive_s)
                except asyncio.TimeoutError:
                    yield None       # keepalive tick
                    continue
                yield event
                if event.get("type") in ("pipeline.done", "pipeline.error"):
                    return
        finally:
            # Best-effort cleanup. If a publisher beats us to creating
            # a new entry under the same id, that one survives.
            current = self._entries.get(request_id)
            if current is entry:
                self._entries.pop(request_id, None)

    # ── Redis backend (Fix #78) ───────────────────────────────────

    async def _publish_redis(self, request_id: str, event: StreamEvent) -> None:
        """Push event to Redis history list AND Pub/Sub channel.

        Order: RPUSH (history) before PUBLISH (channel) guarantees that
        any subscriber that subscribes to the channel AFTER our rpush will
        see the event in history — no events are silently lost.

        Args:
            request_id: Pipeline correlation id (used as the key suffix).
            event:      Serialisable StreamEvent dict.
        """
        try:
            serialized  = json.dumps(event, default=str)
            channel     = f"events:{request_id}"
            history_key = f"event_history:{request_id}"
            pipe = self._redis.pipeline()
            pipe.rpush(history_key, serialized)
            pipe.expire(history_key, _REDIS_HISTORY_TTL)
            pipe.publish(channel, serialized)
            await pipe.execute()
        except Exception as exc:
            logger.warning(
                "event_bus.redis | _publish_redis failed | request_id=%s err=%s",
                request_id, exc,
            )

    async def _subscribe_redis(
        self,
        request_id:      str,
        keepalive_s:     float,
        require_existing: bool,
    ) -> AsyncIterator[Optional[StreamEvent]]:
        """Subscribe using Redis Pub/Sub with history-list replay.

        Ordering guarantees no events are dropped or double-yielded:

            1. Subscribe to Pub/Sub channel (starts buffering live messages).
            2. LRANGE history_key (replay everything published before now).
            3. Drain the Pub/Sub buffer (those messages are duplicates of
               history — both were written in the same RPUSH+PUBLISH pipe).
            4. Yield genuinely new messages from the channel.

        This is safe because _publish_redis does RPUSH before PUBLISH.
        Any message in the Pub/Sub buffer that arrived after step 1 but
        before step 2 is already in the history list and is replayed in
        step 2; step 3 discards it from the buffer to avoid duplication.

        Args:
            request_id:       Pipeline correlation id.
            keepalive_s:      Timeout before yielding a None keepalive tick.
            require_existing: Return immediately when no history exists.
        """
        channel     = f"events:{request_id}"
        history_key = f"event_history:{request_id}"

        # 1. Subscribe first — begin buffering live messages.
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)

        try:
            # 2a. require_existing guard — mirror the in-memory path.
            if require_existing:
                history_len = await self._redis.llen(history_key)
                if history_len == 0:
                    logger.info(
                        "event_bus.subscribe_redis | no history require_existing=True "
                        "| request_id=%s; closing",
                        request_id,
                    )
                    return

            # 2b. Replay full history (may be 0 items if pipeline just started).
            history = await self._redis.lrange(history_key, 0, -1)
            for raw in history:
                try:
                    ev: StreamEvent = json.loads(
                        raw.decode() if isinstance(raw, bytes) else raw
                    )
                except (json.JSONDecodeError, AttributeError):
                    continue
                yield ev
                if ev.get("type") in ("pipeline.done", "pipeline.error"):
                    return

            # 3. Drain Pub/Sub buffer — discard duplicates that overlap history.
            while True:
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=0,
                )
                if msg is None:
                    break

            # 4. Live messages — yield until pipeline finishes or caller exits.
            while True:
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=keepalive_s,
                )
                if msg is None:
                    yield None   # keepalive tick — no message in keepalive_s window
                    continue

                try:
                    data = msg["data"]
                    ev = json.loads(data.decode() if isinstance(data, bytes) else data)
                except (json.JSONDecodeError, KeyError, AttributeError):
                    continue

                yield ev
                if ev.get("type") in ("pipeline.done", "pipeline.error"):
                    return

        finally:
            try:
                await pubsub.unsubscribe(channel)
            except Exception:
                pass
            try:
                await pubsub.aclose()
            except Exception:
                pass

    # ── lifecycle / housekeeping ────────────────────────────────

    def _get_or_create(self, request_id: str) -> _BusEntry:
        entry = self._entries.get(request_id)
        if entry is None:
            entry = _BusEntry()
            self._entries[request_id] = entry
        return entry

    async def _gc_loop(self) -> None:
        """Drop entries whose queues haven't been touched in `_IDLE_TIMEOUT_S`."""
        while True:
            try:
                await asyncio.sleep(_GC_INTERVAL_S)
                now = time.monotonic()
                dead = [
                    rid for rid, e in self._entries.items()
                    if now - e.last_touch > _IDLE_TIMEOUT_S
                ]
                for rid in dead:
                    self._entries.pop(rid, None)
                if dead:
                    logger.info("event_bus.gc | reaped %d idle queues", len(dead))
            except asyncio.CancelledError:
                return
            except Exception as e:  # pragma: no cover — defensive
                logger.warning("event_bus.gc | loop error %s", e)

    def start_gc(self) -> None:
        """Spawn the GC task. Idempotent."""
        if self._gc_task is None or self._gc_task.done():
            self._gc_task = asyncio.create_task(
                self._gc_loop(), name="event_bus_gc",
            )

    def stop_gc(self) -> None:
        if self._gc_task is not None:
            self._gc_task.cancel()
            self._gc_task = None

    # Diagnostics — handy for debugging without poking internals.
    def active_request_ids(self) -> list[str]:
        return list(self._entries.keys())


# ── Module-level singleton ───────────────────────────────────────

_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Return the per-process EventBus, creating it if needed."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
