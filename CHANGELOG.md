# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-05-09

### Added
- Initial project scaffolding and structure
- PostgreSQL database schema with enums, users, policies, and claims tables
- MCP servers for claims, policies, and users domains
- AI orchestrator with analyst and auditor agents
- Observability layer with logging, metrics, and tracers (LangSmith, Langfuse)
- Kubernetes and Docker infrastructure templates
- Shared common package with schemas and enums
- Comprehensive AI rules and operational standards

### Planned
- PDF document extraction MCP
- Validation pipeline with self-healing retry logic
- Coverage reasoning and decision logic for agents
- HTTP API endpoints for claim submission and management
- Database migration system (Alembic)
- Unit and integration tests
- CI/CD pipeline (GitHub Actions)
- Agent evaluation and monitoring

## [Unreleased]

### Security — Fix A–E (2026-05-23)

Resolved 14 second-order vulnerabilities introduced by the Master Patch
waves (B2.x / C2.x). All fixes were merged to `dev`.

**Fix A — Request-ID isolation + Redis-backed ownership registry**
- `RequestOwnerRegistry` rewritten: async `set()`/`get()`, Redis backend
  (`SET owner:{id} EX 3600 NX`), `OwnerConflictError` on overwrite, TTL
  eviction. `request_id` is always a fresh `uuid4()` — never derived from
  `X-Trace-Id`. Namespace drift in `_assert_actor_may_submit_for` corrected
  (`"users"` → `"legacy-provider"`). PDF path validated via `os.path.isabs()`
  + `..` component check before the MCP call.

**Fix B — Pydantic-layer PDF path validation**
- `ClaimSubmitRequest.pdf_path` gains a `@field_validator` that checks for
  null bytes, `..` traversal components, and absolute path requirement.
  Complements the route-level guard added in Fix A.

**Fix C — MCP tool error envelope + kind-based dispatch**
- All MCP servers now tag every error response with `"kind": "business"` or
  `"kind": "internal"`. The orchestrator's `call_mcp_tool` dispatches on
  `kind`: internal errors raise `MCPClientError` (never forwarded to client);
  business errors raise `ValueError` (mapped to a safe 422/404).

**Fix D — Idempotency key includes reason hash; Redis-backed approval state**
- `validate_approval_input` and `record_approval_action` are now async.
- Idempotency key extended to `(actor, claim_id, action, sha256(reason)[:16])`
  — different reason texts generate independent audit-trail entries.
- Both idempotency cache and rate-limit window use Redis sorted sets when
  `REDIS_URL` is set; in-memory fallback preserved for local dev.
- `configure_redis(client)` injected at app startup via lifespan hook.
  `REDIS_URL` stored in `.env` only (Rule 8).

**Fix E — UNTRUSTED_INPUT boundary + TriageReport schema loosening**
- `ANALYST_SYSTEM_PROMPT` gains a `# UNTRUSTED INPUT` section: all tool
  responses are declared untrusted external data; the model is instructed to
  ignore any instruction-like text inside tool outputs.
- `TriageReport.model_config`: `extra="forbid"` → `extra="ignore"` to prevent
  false-positive retry loops from benign auxiliary LLM keys. `strict=True`
  preserved. `ClaimDecision` and `ClaimProcessingResult` keep `extra="forbid"`.

### Planned Features
- Multi-agent claim triage automation
- Real-time claim status tracking
- Auditor approval workflow
- Claim escalation to manual review
- PDF report parsing and entity extraction
- Policy coverage matching
- Decision audit trail

---

**Guidelines for future entries:**
- Use ISO 8601 date format (YYYY-MM-DD)
- Group changes by: Added, Changed, Deprecated, Removed, Fixed, Security
- Reference ticket IDs and commit SHAs where applicable
- Keep entries concise and user-focused
