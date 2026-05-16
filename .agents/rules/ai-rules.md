---
trigger: always_on
---

# AI IDE Rules — Insurance Automation System

## Rule 1 — Monorepo Structure & Shared Code
All Pydantic schemas, ENUMs (`policy_status`, `severity_label`, `claim_status`), and shared exceptions live exclusively in `packages/common/`. No service (`ai-orchestrator`, `mcp-servers/*`) may redefine or duplicate them locally. If a field changes, it changes in one place only.

## Rule 2 — Database Integrity
Use PostgreSQL exclusively. All monetary fields (`deductible_usd`, `coverage_limit_usd`, `reported_damage_amount`) must be `NUMERIC`, never `FLOAT`. The `updated_at` field is always managed by the DB trigger — never set manually in application code. The orchestrator never queries the DB directly; all DB access goes through an MCP server.

## Rule 3 — MCP Domain Boundaries
Each MCP server (`claims/`, `policies/`, `users/`) owns exactly one domain and never imports from another MCP. MCPs are stateless and read-only by default; write operations require explicit `@write_restricted` decorator and are only callable by `auditor_agent`. External integrations (Stripe, SendGrid, etc.) always get their own dedicated MCP — never added into a domain MCP.

## Rule 4 — Agent Responsibilities & ReAct Pattern
`analyst_agent` exclusively handles extraction, triage, and initial reasoning (READ-ONLY). `auditor_agent` exclusively handles review, constraint validation, and final decisions (READ + restricted WRITE). Both agents follow the ReAct pattern: think → act → observe → reflect. Agent reasoning must always be logged and included in `ai_reasoning` before any output reaches the user.

## Rule 5 — Self-Healing Validation Pipeline
All agent output passes through `validation_pipeline.py` before reaching the user. If output violates a business constraint, the pipeline feeds the error back to the agent with a clear explanation and the agent retries — maximum 3 attempts. After 3 failures, the claim escalates to `MANUAL_REVIEW`. All retry attempts are logged with timestamps and error descriptions in `observability/logging.py`.

## Rule 6 — User Approval for Critical Actions
Any operation that creates, modifies, or deletes data (INSERT, UPDATE, DELETE) must pause and request explicit user confirmation before executing. The flow is: agent generates reasoning summary → displays to user → awaits explicit approval ("yes" / "confirm") → executes. Silence or timeout (5 minutes) equals automatic rejection. All confirmations are logged with timestamp, user ID, agent reasoning, and final decision.

## Rule 7 — Security: Auth, CORS & SSE
All API endpoints require JWT authentication (`Authorization: Bearer <token>`). JWT payload must include `user_id`, `role`, and `exp`. Token validation happens once in `validation_pipeline.py`, never per-route. CORS is restricted to whitelisted domains only from `CORS_ALLOWED_ORIGINS` in `.env` — never wildcard `*`. All MCP ↔ orchestrator communication uses Server-Sent Events (SSE) exclusively — never gRPC or persistent sockets. Role-based access: `analyst` → READ MCPs only; `auditor` → READ + restricted WRITE; `admin` → full access. Enforcement happens at `mcp_router.py`.

## Rule 8 — Environment & Secrets Management
All sensitive data (API keys, DB credentials, ports, service URLs) lives in `.env` exclusively — never hardcoded. `.env` is gitignored; `.env.example` is committed with placeholder values. `secrets.yaml` in `mcp-servers/config/` references environment variables only — never stores raw secrets. Port configuration always comes from `.env`.

## Rule 9 — Observability & Tracing
Never call LangSmith, LangFuse, or any tracing SDK directly inside agents or MCPs. All tracing goes through `observability/tracers/base_tracer.py`. New tracer implementations are new files in `observability/tracers/` — no refactoring of existing code. All logging uses the logger from `observability/logging.py` — no `print()` statements anywhere in the codebase.

## Rule 10 — Infrastructure & Containerization
Every new MCP must have its own `Dockerfile.mcp-<domain>` in `infra/docker/` and its own folder (`deployment.yaml` + `service.yaml`) in `infra/k8s/`. `docker-compose.yml` is for local development only — never referenced in production configs. `infra/` is infrastructure-as-code territory (YAML); `deployment/` is Python application-level only. These never cross-reference each other.

## Rule 11 — Code Quality & Type Safety
Python type hints are mandatory on every function signature. All inter-service data must be validated through a Pydantic model from `packages/common/` before processing. No raw `dict` passing between services. Every function that performs a DB operation, agent call, or external API request must be wrapped in structured error handling and logged via `observability/logging.py`.

Rule 12 — Monorepo Dependencies via Root requirements.txt
The single root requirements.txt must include all dependencies for every service (ai-orchestrator, mcp-servers/*, observability, deployment, pipelines). packages/common/ is installed as a local editable package at the top of the file (-e ./packages/common). Each Dockerfile copies the root requirements.txt and installs it entirely — no per-service filtering. Dependencies are pinned to exact versions. When a new dependency is needed, it's added to the root requirements.txt and all services inherit it on next build.
