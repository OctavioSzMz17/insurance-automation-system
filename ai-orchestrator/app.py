"""FastAPI Orchestrator — Phase C C1.1

Top-level FastAPI app. Boots the AgentRouter once at startup (lifespan
hook) and reuses it for every request. Mounts health + claims routers.

Run:
    uvicorn app:app --app-dir ai-orchestrator --reload
    # or
    py scripts/run_api.py

Endpoints exposed by C1.1:
    GET  /health           — liveness
    GET  /ready            — readiness (LLM / DB config checks)
    POST /claims/submit    — full agent pipeline
    GET  /docs             — auto-generated OpenAPI

NOT yet:
    - Auth (C1.4)
    - CORS (added here but defaults to a permissive setting in development)
    - GET /claims/{id} (C1.2)
    - POST /claims/{id}/approve (C1.3)

Rules applied:
    Rule 7  — Will add JWT + RBAC in C1.4
    Rule 9  — Structured logging via stdlib logger
    Rule 11 — Type hints everywhere
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_HERE))

from config import get_settings                       # noqa: E402
from routes.agent_router import AgentRouter           # noqa: E402
from routes.mcp_router   import MCPRouter             # noqa: E402
from routes import health as health_routes            # noqa: E402
from routes import claims as claims_routes            # noqa: E402
from routes import auth   as auth_routes              # noqa: E402
from routes import users  as users_routes             # noqa: E402

from middleware.trace_id        import TraceIdMiddleware     # noqa: E402
from observability.logging      import configure_logging     # noqa: E402
from observability.event_bus    import get_event_bus         # noqa: E402
# Master Patch Part 2 — bounded, thread-safe request-owner registry.
from auth.request_owners        import RequestOwnerRegistry  # noqa: E402
# Master Patch Part 4 — bounded, per-sub-quota stream-ticket store.
from auth.stream_tickets        import StreamTicketStore     # noqa: E402
# Fix A — optional Redis client for shared state across replicas.
from auth.redis_client          import create_redis_client   # noqa: E402
# Fix D — inject Redis into approval validator at startup.
from validation                 import configure_approval_redis  # noqa: E402


# ── Logging — D3.1 ──────────────────────────────────────────────
# Single entry point owns level / format / trace filter. The format is
# selectable at runtime via LOG_FORMAT=text|json so devs get readable
# logs and prod / aggregators get structured JSON.
_settings = get_settings()
configure_logging(
    level=_settings.log_level,
    log_format=("json" if _settings.log_format.lower() == "json" else "text"),
)
logger = logging.getLogger("orchestrator")


# ── Lifespan: build the singleton AgentRouter once at startup ─────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown hooks for the FastAPI app."""
    settings = get_settings()
    logger.info(
        "Orchestrator starting | env=%s provider=%s model=%s",
        settings.environment, settings.llm_provider, settings.model_name,
    )

    # Fix A — initialise Redis client (None when REDIS_URL is not set).
    redis_client = await create_redis_client(settings.redis_url)
    app.state.redis = redis_client
    if redis_client is not None:
        logger.info("Redis connected | url=<redacted> backend=shared-state")
    else:
        logger.warning(
            "REDIS_URL not set — ownership/ticket stores running in-memory "
            "(single-replica only; set REDIS_URL for multi-replica deployments)"
        )

    try:
        app.state.mcp_router = MCPRouter()
        logger.info("MCPRouter ready")
    except Exception as e:
        logger.error("MCPRouter failed to initialise: %s", e, exc_info=True)
        app.state.mcp_router = None

    try:
        app.state.agent_router = AgentRouter(max_attempts=settings.max_retry_attempts)
        logger.info("AgentRouter ready")
    except Exception as e:
        logger.error("AgentRouter failed to initialise: %s", e, exc_info=True)
        app.state.agent_router = None

    # E1.1 — start the event-bus GC so orphaned per-request queues
    # don't pile up if a publisher never gets a subscriber.
    bus = get_event_bus()
    bus.start_gc()
    app.state.event_bus = bus
    logger.info("EventBus ready (gc=on)")

    # Master Patch Part 4 — bounded, thread-safe stream-ticket store.
    # Eradicates the plain dict that allowed unbounded growth + O(N)
    # sweep amplification. Per-sub quota blocks single-credential
    # mint floods.
    # Fix D — wire Redis into the approval validator module-level client.
    configure_approval_redis(redis_client)
    logger.info("ApprovalValidators Redis configured")

    # Fix A — pass Redis client so stores are shared across replicas.
    app.state.stream_tickets = StreamTicketStore(
        maxsize=10_000,
        ttl_seconds=30,
        max_per_sub=20,
        redis_client=redis_client,
    )
    logger.info("StreamTicketStore ready (maxsize=10000 ttl=30s per_sub=20)")

    # Master Patch Part 2 + Fix A — ownership registry backed by Redis
    # when available so cross-replica tenant isolation is enforced.
    app.state.request_owners = RequestOwnerRegistry(
        maxsize=10_000,
        ttl_seconds=3600,
        redis_client=redis_client,
    )
    logger.info("RequestOwnerRegistry ready (maxsize=10000 ttl=3600s)")

    yield

    bus.stop_gc()

    # Fix A — close Redis connection cleanly on shutdown.
    if redis_client is not None:
        await redis_client.aclose()
        logger.info("Redis connection closed")

    logger.info("Orchestrator shutting down")
    app.state.agent_router = None
    app.state.mcp_router = None


# ── App ───────────────────────────────────────────────────────────

app = FastAPI(
    title="InsureAI Orchestrator",
    description=(
        "Insurance claim triage API — runs PDF claims through the "
        "analyst + auditor agent pipeline and returns structured decisions."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# D2.4 — bind a trace_id to every request as the first thing we do.
# IMPORTANT: register BEFORE CORS so the contextvar is set even on
# OPTIONS preflight requests (helps when correlating CORS bugs).
app.add_middleware(TraceIdMiddleware)

# Permissive CORS for development — C1.4 / C1.5 will tighten this.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-Id"],  # let browsers read it for debugging
)

# Mount routers
app.include_router(health_routes.router)
app.include_router(auth_routes.router)
app.include_router(claims_routes.router)
app.include_router(users_routes.router)


# Convenience root
@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"service": "insureai-orchestrator", "docs": "/docs"}
