"""Background reaper: expired HITL proposals → MANUAL_REVIEW claims (Rule 6 timeout).

Every `interval` seconds the reaper scans hitl:tracking for proposals whose score
(= expiry timestamp) is in the past. For each expired entry it:
  1. Fetches the stored claim fields from hitl:tracking:data:{proposal_id}.
  2. Calls create_claim(..., status="MANUAL_REVIEW") via the MCP pool.
  3. Removes the tracking entry and data key on success.

If the data key is absent (human already approved/rejected concurrently, or the
2× TTL ran out), the tracking entry is removed as an orphan — no MANUAL_REVIEW is
created (claim was already handled or data is unrecoverable).

All errors during claim creation are logged and swallowed so one bad entry can't
stop the loop from processing the rest.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from redis.asyncio import Redis

from core.mcp_client import MCPClientPool
from core.service_identity import HITL_REAPER_SUB, SERVICE_ROLE
from core.tools import dispatch

_TRACKING_SET = "hitl:tracking"
_TRACKING_DATA_PREFIX = "hitl:tracking:data:"
# Single source of truth for service principals — see core/service_identity.py.
_REAPER_SUB = HITL_REAPER_SUB
_REAPER_ROLE = SERVICE_ROLE
_DEFAULT_INTERVAL = 60.0

logger = logging.getLogger(__name__)


async def _process_expired(redis: Redis, pool: MCPClientPool, now: float) -> int:
    """Scan hitl:tracking for entries with score <= now and create MANUAL_REVIEW claims.

    Returns the number of entries successfully processed.
    """
    raw_members = await redis.zrangebyscore(_TRACKING_SET, "-inf", now)
    if not raw_members:
        return 0

    processed = 0
    for raw in raw_members:
        proposal_id = raw.decode() if isinstance(raw, bytes) else raw
        data_key = f"{_TRACKING_DATA_PREFIX}{proposal_id}"

        raw_data = await redis.get(data_key)
        if raw_data is None:
            # Human acted first (or data TTL expired) — remove orphaned tracking entry.
            await redis.zrem(_TRACKING_SET, proposal_id)
            logger.debug("hitl reaper removed orphan | proposal_id=%s", proposal_id[:8])
            continue

        entry = json.loads(raw_data.decode() if isinstance(raw_data, bytes) else raw_data)
        try:
            await dispatch(
                pool,
                "claims__create_claim",
                {
                    "submitted_by_sub": entry["submitted_by_sub"],
                    "policy_id": entry["policy_id"],
                    "pdf_sha256": entry["pdf_sha256"],
                    "reported_damage_amount": entry["reported_damage_amount"],
                    "severity": entry["severity"],
                    "status": "MANUAL_REVIEW",
                    "decision_metadata": {
                        "reason": "hitl_timeout",
                        "proposal_id": proposal_id,
                    },
                    "caller_sub": _REAPER_SUB,
                    "caller_role": _REAPER_ROLE,
                },
                allow_writes=True,
            )
        except Exception as exc:
            logger.error(
                "hitl reaper failed to create MANUAL_REVIEW | proposal_id=%s err=%s",
                proposal_id[:8], exc,
            )
            continue

        await redis.zrem(_TRACKING_SET, proposal_id)
        await redis.delete(data_key)
        processed += 1
        logger.info(
            "hitl timeout → MANUAL_REVIEW | proposal_id=%s sub=%s",
            proposal_id[:8], entry.get("submitted_by_sub"),
        )

    return processed


async def run_reaper(
    redis: Redis | None,
    pool: MCPClientPool,
    *,
    interval: float = _DEFAULT_INTERVAL,
) -> None:
    """Loop indefinitely, processing expired HITL entries every `interval` seconds.

    Designed to run as an asyncio.Task in the app lifespan. Stops cleanly on
    CancelledError. If redis is None the loop runs but skips every cycle (fail-open).
    """
    logger.info("hitl reaper started | interval=%.0fs", interval)
    while True:
        try:
            await asyncio.sleep(interval)
            if redis is None:
                continue
            count = await _process_expired(redis, pool, time.time())
            if count:
                logger.info("hitl reaper cycle complete | processed=%d", count)
        except asyncio.CancelledError:
            logger.info("hitl reaper stopped")
            return
        except Exception as exc:
            logger.error("hitl reaper cycle error | err=%s", exc)
