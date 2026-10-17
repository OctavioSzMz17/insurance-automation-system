"""FastAPI orchestrator entrypoint (Phase 4: JWT auth + public HTTP API).

Endpoints (all require Bearer JWT unless noted):
  GET  /health             open — liveness probe
  GET  /ready              open — readiness probe
  POST /auth/token         open in dev, 404 in prod — issue test JWT
  POST /auth/logout        JWT — revoke caller's token
  POST /auth/revoke-user/{sub}  JWT (admin) — revoke all tokens for user
  POST /analyze            JWT (analyst+)
  POST /audit              JWT (auditor+)
  GET  /pending            JWT (analyst+)
  POST /confirm            JWT (auditor+)
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager, suppress

from config import get_settings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agents.analyst import Analyst
from agents.auditor import Auditor
from agents.mutation_handler import MutationHandler
from auth.stream_tickets import StreamTicketStore
from core.hitl import HITLStore
from core.logging_bridge import configure as configure_logging
from core.logging_bridge import get_logger
from core.mcp_client import MCPClientPool
from core.models import get_model
from core.redis_bridge import dispose_redis, get_redis
from middleware.rate_limit import RateLimitMiddleware
from middleware.trace_id import TraceIdMiddleware
from observability import configure_tracing, shutdown_tracing
from routes.agent_router import router as agent_router
from routes.auth import router as auth_router
from routes.health import router as health_router
from routes.sse import router as sse_router
from routes.upload import router as upload_router
from workers.hitl_reaper import run_reaper

_logger = get_logger(__name__)

# ── CORS config read from env at import time (safe — no validators run) ───────
_cors_origins = [
    o.strip()
    for o in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:8000").split(",")
    if o.strip()
]


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.cross_validate()

    tracer = configure_tracing(
        settings.observability_backend,
        environment=settings.environment,
        mask_level=settings.observability_mask or None,
    )

    pool = MCPClientPool(settings)
    await pool.start()
    llm = get_model(settings.analyst_model_tier)
    redis = await get_redis(settings.redis_url)
    hitl = HITLStore(redis, default_ttl=settings.analyst_proposal_ttl_seconds)

    app.state.settings = settings
    app.state.pool = pool
    app.state.analyst = Analyst(runtime=settings, pool=pool, llm=llm)
    app.state.auditor = Auditor(runtime=settings, pool=pool, llm=llm)
    app.state.mutation = MutationHandler(runtime=settings, pool=pool)
    app.state.hitl = hitl
    app.state.redis = redis
    app.state.ticket_store = StreamTicketStore(redis_client=redis)

    reaper_task = asyncio.create_task(run_reaper(redis, pool))

    _logger.info(
        "orchestrator boot",
        extra={
            "event": "orchestrator.boot",
            "tier": settings.analyst_model_tier,
            "model": llm.model,
            "tracing": type(tracer).__name__,
        },
    )
    try:
        yield
    finally:
        reaper_task.cancel()
        with suppress(asyncio.CancelledError):
            await reaper_task
        await pool.stop()
        await dispose_redis()
        # Last, so spans emitted while shutting down are included in the flush.
        shutdown_tracing()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="insurance-orchestrator", version="0.4.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(TraceIdMiddleware)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(sse_router)
app.include_router(upload_router)
app.include_router(agent_router)
