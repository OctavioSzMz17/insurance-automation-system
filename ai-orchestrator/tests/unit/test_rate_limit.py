"""Unit tests for middleware/rate_limit.py — sliding window logic + middleware."""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.rate_limit import RateLimitMiddleware, _peek_jwt_sub, _rate_key, check_sliding_window


# ── _peek_jwt_sub ─────────────────────────────────────────────────────────────

def test_peek_valid_jwt_returns_sub():
    # Craft a minimal JWT payload without signing
    import base64, json
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "alice", "role": "analyst"}).encode()).rstrip(b"=").decode()
    fake_token = f"header.{payload}.signature"
    assert _peek_jwt_sub(fake_token) == "alice"


def test_peek_invalid_jwt_returns_none():
    assert _peek_jwt_sub("not-a-jwt") is None
    assert _peek_jwt_sub("") is None
    assert _peek_jwt_sub("a.b") is None  # only 2 parts


def test_peek_jwt_without_sub_returns_none():
    import base64, json
    payload = base64.urlsafe_b64encode(json.dumps({"role": "analyst"}).encode()).rstrip(b"=").decode()
    fake_token = f"header.{payload}.signature"
    assert _peek_jwt_sub(fake_token) is None


# ── check_sliding_window ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sliding_window_allows_within_limit():
    """First 3 requests with limit=3 should all be allowed."""
    redis = _make_fake_redis()
    for i in range(3):
        allowed, count = await check_sliding_window(redis, "rate:test:alice", limit=3, window=60)
        assert allowed, f"request {i+1} should be allowed"
    assert count == 3


@pytest.mark.asyncio
async def test_sliding_window_blocks_over_limit():
    """4th request with limit=3 should be blocked."""
    redis = _make_fake_redis()
    for _ in range(3):
        await check_sliding_window(redis, "rate:test:bob", limit=3, window=60)
    allowed, count = await check_sliding_window(redis, "rate:test:bob", limit=3, window=60)
    assert not allowed
    assert count == 4


@pytest.mark.asyncio
async def test_sliding_window_different_keys_independent():
    redis = _make_fake_redis()
    for _ in range(3):
        await check_sliding_window(redis, "rate:test:alice", limit=3, window=60)
    # alice is at limit, but bob is fresh
    allowed_bob, _ = await check_sliding_window(redis, "rate:test:bob", limit=3, window=60)
    assert allowed_bob


# ── RateLimitMiddleware ───────────────────────────────────────────────────────

def _make_app_with_limit(redis, limit: int = 3, window: int = 60) -> FastAPI:
    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        settings = MagicMock()
        settings.rate_limit_requests = limit
        settings.rate_limit_window_seconds = window
        app.state.redis = redis
        app.state.settings = settings
        yield

    app = FastAPI(lifespan=_lifespan)
    app.add_middleware(RateLimitMiddleware)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    return app


def test_middleware_allows_within_limit():
    redis = _make_fake_redis()
    app = _make_app_with_limit(redis, limit=3)
    with TestClient(app) as c:
        for i in range(3):
            resp = c.get("/ping")
            assert resp.status_code == 200, f"request {i+1} should succeed"
            assert "X-RateLimit-Limit" in resp.headers
            assert resp.headers["X-RateLimit-Limit"] == "3"


def test_middleware_blocks_over_limit():
    redis = _make_fake_redis()
    app = _make_app_with_limit(redis, limit=3)
    with TestClient(app) as c:
        for _ in range(3):
            c.get("/ping")
        resp = c.get("/ping")
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    assert resp.headers["X-RateLimit-Remaining"] == "0"


def test_middleware_fail_open_when_redis_none():
    """No Redis configured → all requests pass through."""
    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        settings = MagicMock()
        settings.rate_limit_requests = 1
        settings.rate_limit_window_seconds = 60
        app.state.redis = None   # no Redis
        app.state.settings = settings
        yield

    app = FastAPI(lifespan=_lifespan)
    app.add_middleware(RateLimitMiddleware)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    with TestClient(app) as c:
        for _ in range(5):
            resp = c.get("/ping")
            assert resp.status_code == 200


def test_middleware_fail_open_on_redis_error():
    """Redis error during check → fail-open (allow request)."""
    redis = AsyncMock()
    redis.pipeline.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
    error_pipe = AsyncMock()
    error_pipe.zremrangebyscore = AsyncMock()
    error_pipe.zadd = AsyncMock()
    error_pipe.zcard = AsyncMock()
    error_pipe.expire = AsyncMock()
    error_pipe.execute = AsyncMock(side_effect=ConnectionError("Redis down"))
    redis.pipeline.return_value = error_pipe

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        settings = MagicMock()
        settings.rate_limit_requests = 1
        settings.rate_limit_window_seconds = 60
        app.state.redis = redis
        app.state.settings = settings
        yield

    app = FastAPI(lifespan=_lifespan)
    app.add_middleware(RateLimitMiddleware)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    with TestClient(app) as c:
        resp = c.get("/ping")
    assert resp.status_code == 200


def test_middleware_uses_sub_from_jwt():
    """Requests with different JWT subs have independent rate limits."""
    import base64, json as _json

    def _jwt_for(sub: str) -> str:
        payload = base64.urlsafe_b64encode(
            _json.dumps({"sub": sub, "role": "analyst"}).encode()
        ).rstrip(b"=").decode()
        return f"header.{payload}.sig"

    redis = _make_fake_redis()
    app = _make_app_with_limit(redis, limit=2)

    with TestClient(app) as c:
        # alice hits limit
        for _ in range(2):
            c.get("/ping", headers={"Authorization": f"Bearer {_jwt_for('alice')}"})
        resp_alice = c.get("/ping", headers={"Authorization": f"Bearer {_jwt_for('alice')}"})
        # bob is fresh — different key
        resp_bob = c.get("/ping", headers={"Authorization": f"Bearer {_jwt_for('bob')}"})

    assert resp_alice.status_code == 429
    assert resp_bob.status_code == 200


# ── In-memory fake Redis for tests ───────────────────────────────────────────


def _make_fake_redis():
    """Minimal in-memory Redis fake that supports zremrangebyscore/zadd/zcard/expire/pipeline."""
    store: dict[str, dict[str, float]] = {}  # key → {member: score}
    expirations: dict[str, float] = {}

    class FakePipeline:
        def __init__(self):
            self._ops = []

        def zremrangebyscore(self, key, min_score, max_score):
            self._ops.append(("zremrangebyscore", key, float("-inf") if min_score == "-inf" else float(min_score), float(max_score)))
            return self

        def zadd(self, key, mapping):
            self._ops.append(("zadd", key, mapping))
            return self

        def zcard(self, key):
            self._ops.append(("zcard", key))
            return self

        def expire(self, key, ttl):
            self._ops.append(("expire", key, ttl))
            return self

        async def execute(self):
            results = []
            for op in self._ops:
                if op[0] == "zremrangebyscore":
                    _, key, min_s, max_s = op
                    if key not in store:
                        store[key] = {}
                    to_remove = [m for m, s in store[key].items() if min_s <= s <= max_s]
                    for m in to_remove:
                        del store[key][m]
                    results.append(len(to_remove))
                elif op[0] == "zadd":
                    _, key, mapping = op
                    if key not in store:
                        store[key] = {}
                    added = 0
                    for m, s in mapping.items():
                        if m not in store[key]:
                            added += 1
                        store[key][m] = s
                    results.append(added)
                elif op[0] == "zcard":
                    _, key = op
                    results.append(len(store.get(key, {})))
                elif op[0] == "expire":
                    _, key, ttl = op
                    expirations[key] = time.time() + ttl
                    results.append(1)
            return results

    class FakeRedis:
        def pipeline(self):
            return FakePipeline()

    return FakeRedis()
