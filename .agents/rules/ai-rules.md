---
trigger: always_on
---

# AI IDE Rules — Insurance Automation System

## Rule 1 — Monorepo Structure & Shared Code
All Pydantic schemas, ENUMs (`policy_status`, `severity_label`, `claim_status`), and shared exceptions live exclusively in `packages/common/`. No service (`ai-orchestrator`, `mcp-servers/*`) may redefine or duplicate them locally. If a field changes, it changes in one place only.

- `ClaimStateConflictError` and all domain exceptions are imported via `from packages.common.exceptions import ...` — never matched by string name.
- Non-retry exceptions in `mcp_router.py` must use `isinstance()` checks against the imported class, never `_NON_RETRY_CLASS_NAMES` string sets.

## Rule 2 — Database Integrity
Use PostgreSQL exclusively. All monetary fields (`deductible_usd`, `coverage_limit_usd`, `reported_damage_amount`) must be `NUMERIC`, never `FLOAT`. The `updated_at` field is always managed by the DB trigger — never set manually in application code. The orchestrator never queries the DB directly; all DB access goes through an MCP server.

- Every claim row must carry `submitted_by_sub VARCHAR NOT NULL` (the JWT `sub` of the submitting analyst), stamped at INSERT time. This is the row-level ACL anchor.
- Every claim row must carry `decision_metadata JSONB NOT NULL DEFAULT '{}'`. This field is populated at write time with: tool inputs/outputs, model/version, policy snapshot, and decision evidence. Schema versioning is required; event history is immutable.
- SQLAlchemy `Engine` is a **singleton per process** (double-checked lazy init). `create_engine()` is NEVER called inside a tool handler or per-request path. Doing so exhausts the DB connection pool under concurrency.

## Rule 3 — MCP Domain Boundaries
Each MCP server (`claims/`, `policies/`, `users/`) owns exactly one domain and never imports from another MCP. MCPs are stateless and read-only by default; write operations require explicit `@write_restricted` decorator and are only callable by `auditor_agent`. External integrations (Stripe, SendGrid, etc.) always get their own dedicated MCP — never added into a domain MCP.

**Transport enforcement — CRITICAL:**
- The orchestrator NEVER imports MCP tool files directly by path (`from mcp-servers.claims.tools import ...` is forbidden).
- ALL MCP ↔ orchestrator communication goes through out-of-process SSE transport only.
- No `file-loader` or local module invocation path exists in production code.
- In `_loader.py` and `mcp_router.py`, direct file imports of MCP internals are a build-blocking violation.

**Domain isolation:**
- `legacy-provider` must NOT bundle users and policies domains in one service. They must be split into separate `users` and `policies` MCP servers with isolated deploy/runtime identities and least-privilege DB credentials.
- All route and router registry references must consistently use `policies.get_policies_by_user_id` — never `legacy-provider` after the split.

## Rule 4 — Agent Responsibilities & ReAct Pattern
`analyst_agent` exclusively handles extraction, triage, and initial reasoning (READ-ONLY). `auditor_agent` exclusively handles review, constraint validation, and final decisions (READ + restricted WRITE). Both agents follow the ReAct pattern: think → act → observe → reflect. Agent reasoning must always be logged and included in `ai_reasoning` before any output reaches the user.

**Prompt injection defense — MANDATORY:**
- Both `analyst_agent` and `auditor_agent` must wrap ALL PDF-derived or user-supplied content in `<UNTRUSTED_INPUT>` tags inside the user message.
- Both `core/prompts/analyst.py` and `core/prompts/auditor.py` must contain a `SECURITY BOUNDARY` block that instructs the LLM to treat `<UNTRUSTED_INPUT>` as untrusted data, never as instructions.
- The `summary` field emitted by the analyst must be sanitized server-side: max 200 characters, non-printable characters stripped. Never relay the raw LLM summary directly to UI.
- Severity classification must be deterministic based on `reported_damage_amount` thresholds — never inferred from LLM-generated text. A PDF cannot influence the severity level.

**Agent write isolation — CRITICAL (see Rule 6):**
- Neither `analyst_agent` nor `auditor_agent` may call `create_claim`, `update_claim_status`, or any write MCP tool during pipeline execution.
- Agents return a **signed proposal object**. Persistence occurs only from the dedicated mutation handler after explicit human approval.

**Schema strictness:**
- `TriageReport` uses `model_config = ConfigDict(extra="ignore", strict=True)` — tolerates LLM verbosity without breaking.
- `ClaimDecision` uses `model_config = ConfigDict(extra="forbid", strict=True)` — strict because it is the security boundary for the auditor.
- All other inter-service data models use `extra="forbid"` unless there is a documented reason.

## Rule 5 — Self-Healing Validation Pipeline
All agent output passes through `validation_pipeline.py` before reaching the user. If output violates a business constraint, the pipeline feeds the error back to the agent with a clear explanation and the agent retries — maximum 3 attempts. After 3 failures, the claim escalates to `MANUAL_REVIEW`. All retry attempts are logged with timestamps and error descriptions in `observability/logging.py`.

**Idempotency key integrity:**
- The idempotency cache key for `_decide_claim` is `(actor, claim_id, action, sha256(reason.strip())[:16])`. The `reason` field is part of the key — omitting it allows silent audit trail tampering.
- Implementation: `hashlib.sha256(reason.strip().encode()).hexdigest()[:16]`.

**Terminal status completeness:**
- `_TERMINAL_STATUSES` must be `{"APPROVED", "REJECTED", "MANUAL_REVIEW"}`. `MANUAL_REVIEW` is a terminal state. A claim in `MANUAL_REVIEW` must never transition back to `AWAITING_INPUT`.
- The `/confirm` endpoint must handle `MANUAL_REVIEW` decisions without returning 404.

## Rule 6 — Human-in-the-Loop (HITL) for ALL Mutations
Any operation that creates, modifies, or deletes regulated data (INSERT, UPDATE, DELETE on claims, policies, users) **must pause and receive explicit human confirmation before executing**. This is a hard compliance requirement.

**Enforcement architecture:**
- Agents generate a reasoning summary → return a signed proposal object → pipeline displays to human → human calls explicit approval endpoint → dedicated mutation handler persists.
- `create_claim` is NEVER called during agent runtime. It is only callable from the dedicated mutation handler after the approval endpoint fires.
- The proposal object must be HMAC-signed to prevent interception and tampering between generation and approval.
- Silence or timeout (5 minutes) = automatic rejection. The claim status moves to `MANUAL_REVIEW`.
- All confirmations are logged with: timestamp, user ID (JWT `sub`), agent reasoning, proposal hash, and final decision.

## Rule 7 — Security: Auth, Transport & Access Control

### JWT Requirements
- Every JWT payload must include: `sub`, `role`, `exp`, `iat`, and `jti` (UUID4). The `jti` enables token revocation.
- `jwt.decode()` must always pass `audience` and `issuer` parameters (read from `JWT_AUDIENCE` and `JWT_ISSUER` in `.env`). A token signed for another service with the same secret must be rejected.
- Token revocation: on `POST /auth/logout`, write `revoked_jti:{jti} = 1 EX {remaining_ttl}` to Redis. Every authenticated request checks Redis for revocation before proceeding.
- `JWT_SECRET_KEY` must be validated at startup: fail with `RuntimeError` if the value is the placeholder `"dev-only-replace-me"` or shorter than 32 characters in any environment. Use asymmetric JWT (JWKS) as the target; symmetric HS256 only with a strong, rotatable secret.
- `/auth/token` (dev token issuance) is disabled if `settings.environment.lower() in {"production", "prod", "staging"}`. Additionally requires `X-Dev-Bootstrap-Secret` header matching `settings.dev_bootstrap_secret`. Refuse to start if `dev_bootstrap_secret` is empty when not in a protected environment.

### SSE Stream Tickets (anti-JWT-in-querystring)
- `?token=<JWT>` in query strings is FORBIDDEN for SSE/EventSource connections. Query-string tokens leak to logs, proxy access logs, browser history, and referrers.
- SSE authentication uses **short-lived one-time stream tickets**: server-mints a UUID ticket, stores it in Redis with `TTL=30s`, narrow `aud` and `sub` binding. Client presents ticket in query string; server consumes (deletes) it immediately on first use.
- Ticket store uses Redis with `SCAN` cursor loops — NEVER `KEYS *` (O(N) blocking operation that freezes Redis under load).

### Request ID Isolation — CRITICAL
- `request_id` (pipeline ID, ownership key, event bus key) is ALWAYS `str(uuid.uuid4())` generated server-side.
- `X-Trace-Id` header is ONLY used for observability correlation (Langfuse, logging). It NEVER becomes `request_id`, is NEVER used as an ownership map key, and is NEVER used to construct file paths.
- The ownership registry (`request_owners`) rejects any overwrite attempt where the existing owner differs from the requester. Once bound, ownership is immutable for the life of the request.
- Path construction for uploads: always `UUID(pipeline_id)` validated first → `(_UPLOAD_DIR / f"{pipeline_id}.pdf").resolve()` → verify `_UPLOAD_DIR.resolve() in target.parents` before writing.

### Endpoint Access Control
- All API endpoints require JWT authentication except `/health` (returns only `{"status": "ok"}`, no env detail).
- `/internal/ready` is authenticated or restricted by network policy to internal probe IPs. It performs real connectivity checks: Redis `PING`, DB query, MCP reachability — not string non-empty checks.
- `/health` and `/ready` must NOT expose dependency posture (model key presence, DB URL, service URLs).
- Row-level ACL on claims: `GET /claims/{id}` and `GET /claims/{id}/trace` return 403 unless `claim.submitted_by_sub == caller.sub` OR `caller.role == ADMIN`. `GET /claims` filters by `submitted_by_sub` for ANALYST role.
- Row-level ACL on users: `GET /users/{id}` and `GET /users/{id}/policies` return 403 unless `caller.sub == user.email` (the requesting user is the subject) OR `caller.role == ADMIN`. ANALYST role cannot enumerate other users' profiles or policies by ID. There is no "list all users" endpoint accessible to ANALYST — only ADMIN. Violating this is an IDOR that exposes every customer's name, email, and policy details.
- CORS: `allow_origins` is always read from `CORS_ALLOWED_ORIGINS` env var (comma-separated string → `cors_origins_list` property splits at runtime). Wildcard `*` is never used in any environment. `cors_allowed_origins` config field is `str`, not `list[str]`, to avoid pydantic-settings JSON-decode failure on `.env` values.

### MCP Transport Auth
- Static shared secret (`X-MCP-Secret`) is acceptable only for local development. Production target is mTLS service identity or short-lived signed service tokens with `aud`, `exp`, and `nonce` claims, plus rotation support and key versioning.
- If static secret is used, `MCP_SHARED_SECRET` must be non-empty at startup or the service refuses to start.

### MCP Session Pooling
- `core/mcp_client.py` maintains a **pool of persistent SSE sessions** per MCP server — one session per server, shared across all tool calls in the same process lifetime. A new HTTP+SSE connection is NEVER opened per tool call.
- Implementation: `_sessions: dict[str, MCPClientSession]` is initialized in app lifespan startup via `asynccontextmanager`. Tool calls acquire the existing session from the pool, they never create one.
- If a session drops (network error, server restart), the client reconnects with exponential backoff before surfacing the error to the caller. Silent reconnect is preferable to crashing the pipeline.
- In tests, session mocks replace the pool entry — never patch `httpx` or the SSE transport directly.

### Error Handling — No Internal Leakage
- MCP servers distinguish error types at the tool layer: `ValueError` → `{"error": str(e), "kind": "business"}`; `RuntimeError` or unexpected → log full detail internally, return `{"error": "upstream_internal_error", "kind": "internal"}`.
- Orchestrator `mcp_client.py` checks `kind`: `"internal"` raises `MCPClientError(public="Upstream internal error")` — the raw SQL/stack trace never reaches the HTTP response.
- `except ValueError as e: raise HTTPException(detail=str(e))` is FORBIDDEN for tool-level errors. Only business-validated messages may be surfaced.

## Rule 8 — Environment & Secrets Management
All sensitive data (API keys, DB credentials, ports, service URLs) lives in `.env` exclusively — never hardcoded. `.env` is gitignored; `.env.example` is committed with placeholder values. `secrets.yaml` in `mcp-servers/config/` references environment variables only — never stores raw secrets.

**Startup validation (fail-hard):**
- `JWT_SECRET_KEY`: fail if placeholder or < 32 chars in any environment.
- `LANGFUSE_ENCRYPTION_KEY`: fail if value is 64 zeros (`"0" * 64`) or empty in production/staging.
- `MCP_SHARED_SECRET`: fail if empty at startup.
- `CLAIM_UPLOAD_DIR`: must not be `/tmp` or a subdirectory of `/tmp` in production/staging. Fail hard at startup if this condition is violated.
- `dev_bootstrap_secret`: must be non-empty when `environment` is not in `{"production", "prod", "staging"}`.

**File path safety:**
- `_assert_pdf_path_safe()` must use `Path.is_relative_to()` (not `str.startswith()`) to correctly handle case-insensitive filesystems and symlinks.
- Every file-path input (from requests or env vars) is resolved with `.resolve()` and checked with `.is_relative_to(allowed_dir)` before any read/write.
- `pdf_path` in `ClaimSubmitRequest` must be validated to be inside `CLAIM_UPLOAD_DIR` via a Pydantic `@field_validator`. The raw JSON-body `pdf_path` variant should be deprecated in favor of `/submit-upload` (multipart) only.

## Rule 9 — Observability & Tracing
Never call LangSmith, LangFuse, or any tracing SDK directly inside agents or MCPs. All tracing goes through `observability/tracers/base_tracer.py`. New tracer implementations are new files in `observability/tracers/` — no refactoring of existing code. All logging uses the logger from `observability/logging.py` — no `print()` statements anywhere in the codebase, including test harnesses, tooling scripts, and evaluation fixtures.

**Trace marker integrity:**
- Trace markers are HMAC-SHA256 signed using `_SIG_BYTES = 16` (128-bit minimum). Using 12 bytes (96-bit) is insufficient for security-critical audit signing.
- Client-supplied `X-Trace-Id` values are NEVER trusted as pipeline identifiers. They are logged for correlation only.
- A client embedding `[trace_id: forged-value]` in PDF text must not affect the audit trail. Audit trace IDs are server-generated and signed.

## Rule 10 — Infrastructure & Containerization
Every new MCP must have its own `Dockerfile.mcp-<domain>` in `infra/docker/` and its own folder (`deployment.yaml` + `service.yaml`) in `infra/k8s/`. `docker-compose.yml` is for local development only — never referenced in production configs. `infra/` is infrastructure-as-code territory (YAML); `deployment/` is Python application-level only. These never cross-reference each other.

**Hardened production images:**
- All Docker images run as a non-root user (`USER appuser` or equivalent).
- Base images are pinned to an exact digest — never `latest` or a floating tag.
- Production images use a read-only root filesystem where possible.
- The default `python:slim` image running as root in production is a security violation.

## Rule 11 — Code Quality & Type Safety
Python type hints are mandatory on every function signature. All inter-service data must be validated through a Pydantic model from `packages/common/` before processing. No raw `dict` passing between services. Every function that performs a DB operation, agent call, or external API request must be wrapped in structured error handling and logged via `observability/logging.py`.

**Async correctness:**
- A function that calls `await` internally MUST be declared `async def`. A `def` function that internally calls `await` via a sync-async bridge is a runtime crash under async servers (uvicorn/starlette). This applies to `_mint_ticket` and any similar helper.
- All `async def` call sites must `await` the coroutine. Missing `await` on an async helper is a build-blocking bug.

## Rule 12 — Monorepo Dependencies via Root requirements.txt
The single root `requirements.txt` must include all dependencies for every service (`ai-orchestrator`, `mcp-servers/*`, `observability`, `deployment`, `pipelines`). `packages/common/` is installed as a local editable package at the top of the file (`-e ./packages/common`). Each Dockerfile copies the root `requirements.txt` and installs it entirely — no per-service filtering. Dependencies are pinned to **exact versions** (`==`). Package-level `pyproject.toml` files must not contain unpinned runtime dependencies (e.g., `pydantic>=2.0.0` is forbidden; use `pydantic==X.Y.Z` or remove and rely on root). When a new dependency is needed, it is added to the root `requirements.txt` and all services inherit it on next build. CI validates this with a lock-file diff check.

## Rule 13 — Input Validation & API Contract
All external inputs (HTTP bodies, headers, file paths, query parameters) are validated before use.

- `ClaimSubmitRequest.pdf_path` is validated by a Pydantic `@field_validator` that: (1) resolves the path, (2) confirms it is inside `CLAIM_UPLOAD_DIR`, (3) confirms the extension is `.pdf`. Invalid paths raise `ValueError` with a safe message — the actual path is not echoed back.
- Multipart upload (`/submit-upload`) is the canonical submission path. The JSON-body `pdf_path` variant is deprecated and will be removed.
- `POST /claims/submit` and `POST /claims/submit-upload` enforce a Redis sliding-window rate limit: 10 submissions per 60 seconds per authenticated user (`ZREMRANGEBYSCORE` + `ZCARD` + `ZADD` pattern).
- Uploaded filenames are never used directly. The storage filename is always `{server_generated_uuid4}.pdf`. `X-Trace-Id` or any user-supplied header NEVER contributes to the storage filename or upload path.
- Path traversal defense: after constructing the target path with `Path.resolve()`, assert `_UPLOAD_DIR.resolve() in target.parents` before opening for write. Raise `HTTPException(400)` if the assertion fails.
- **PDF deduplication:** Before storing an upload or creating a pipeline, compute `SHA-256` of the raw file bytes and store the hash in `claims.pdf_sha256 VARCHAR(64) NOT NULL`. If a row with the same `pdf_sha256` already exists for the same `submitted_by_sub`, reject the submission with `HTTP 409 Conflict` and return the existing `claim_id`. This prevents the same PDF from spawning N identical claims, avoids redundant LLM calls, and is a required migration field alongside `submitted_by_sub`.

## Rule 14 — Concurrency, State & Multi-Replica Safety
All security-critical and coordination state is stored in **Redis**, never in process-local memory. In-memory fallbacks are acceptable only for single-process local development and must be clearly gated by environment.

| State | Redis Key Pattern | Notes |
|---|---|---|
| Request ownership | `request_owner:{request_id}` | Immutable once set; reject overwrite with different owner |
| Stream tickets | `stream_ticket:{ticket_id}` | Single-use, TTL 30s; consumed (deleted) on first use |
| SSE stream count | `stream_count:{sub}` | INCR/DECR atomic; TTL safety for crashed pods |
| Event bus channels | `events:{request_id}` | RPUSH for history + PUBLISH for live; subscriber replays history first |
| Rate limit windows | `rate_submit:{sub}` | ZADD sliding window, 60s window |
| JWT blocklist | `revoked_jti:{jti}` | SET with `EX {remaining_ttl}` |

**Clock discipline:**
- `time.time()` (Unix epoch, float) is used for all cross-process timestamps (`expires_at`, TTL calculations). `time.monotonic()` is process-local and meaningless in Redis or cross-replica contexts — its use in shared state is a bug.

**Ticket enumeration:**
- Counting active tickets per user uses a Redis `SCAN` cursor loop (`count=100` per batch) — NEVER `KEYS *` (O(N) blocking, freezes Redis under load).

**EventBus:**
- Publish: atomic pipeline of `RPUSH events:{id} {payload}` + `PUBLISH events:{id} {payload}`.
- Subscribe: `SUBSCRIBE events:{id}` first (no-miss), then `LRANGE events:{id} 0 -1` for history replay, then deduplicate buffer, then listen for live messages.
- `bus.set_redis(redis_client)` is called in app startup lifespan before any request is served.
