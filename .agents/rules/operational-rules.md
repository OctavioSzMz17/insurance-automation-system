---
trigger: always_on
---

# Operational Rules — Insurance Automation System

## Rule 1 — Git Branching Strategy
Follow trunk-based development with short-lived feature branches. Branch naming convention: `feature/<ticket-id>-short-description`, `fix/<ticket-id>-short-description`, `hotfix/<ticket-id>-short-description`. No direct commits to `main` — ever. All changes go through a Pull Request with at least one peer review approval. `main` is always deployable.

## Rule 2 — Commit Standards
All commits follow Conventional Commits format: `type(scope): description`. Valid types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`, `security`. Scope maps to the service changed: `feat(mcp-claims)`, `fix(orchestrator)`, `test(common)`, `security(auth)`. Commit messages are written in English, present tense, imperative mood. No commit may bundle unrelated changes — one concern per commit. Security fixes use the `security` type and reference the finding number from the audit report.

## Rule 3 — Pull Request Standards
Every PR must include: a description of what changed and why, reference to the ticket/issue, and a testing summary. PRs that touch `packages/common/` require review from at least two engineers — changes there affect the entire monorepo. PRs must pass all CI checks before merging. No self-merging.

**Security-impacting PRs** (any change to `auth/`, `middleware/`, `mcp-servers/_common/`, `config.py`, `app.py`, CORS config, JWT handling, file upload paths, or agent prompts) require an explicit security review comment from a reviewer confirming the change does not introduce a vulnerability from the known finding categories (see ai-rules.md).

## Rule 4 — CI Pipeline (Per Service)
Every push to a feature branch triggers the CI pipeline automatically. Pipeline stages in order:

1. **Lint** — `ruff` or `flake8` must pass with zero warnings
2. **Type Check** — `mypy` must pass with zero errors
3. **Security Scan** — `bandit -r .` must pass with no HIGH or CRITICAL findings; `pip-audit` must report zero known CVEs in pinned dependencies
4. **Dependency Audit** — CI validates that no package-level `pyproject.toml` introduces unpinned runtime dependencies that conflict with or deviate from root `requirements.txt`
5. **Unit Tests** — must pass with minimum 80% coverage
6. **Integration Tests** — must pass against a local PostgreSQL + Redis instance (not mocks)
7. **Build** — Docker image must build successfully with non-root user confirmed in image manifest

All stages must pass. A single failure blocks the PR from merging.

**Async correctness gate:** CI runs `python -m pytest --asyncio-mode=auto` — any `def` function that internally `await`s will surface as a runtime error in tests. This prevents the `_mint_ticket`-class bug (sync function calling async internals) from reaching main.

## Rule 5 — Testing Standards
Unit tests live in `mcp-servers/*/tests/unit/` and mirror the structure of the service they test. Integration tests live in `mcp-servers/*/tests/integration/`. Agent behavior tests live in `ai-orchestrator/tests/`. Test files are named `test_<module_name>.py`. Every new function or MCP tool must have a corresponding unit test. Every new agent action must have an integration test covering at least: happy path, invalid input, and constraint violation.

**Required test coverage for security-critical paths:**

| Path | Required Test Cases |
|---|---|
| JWT decode | valid token, expired, wrong issuer, wrong audience, revoked jti, missing jti |
| Stream ticket mint/consume | valid flow, expired ticket, double-consume (replay), wrong owner |
| File upload path | valid PDF, path traversal via X-Trace-Id, path outside UPLOAD_DIR, non-PDF extension |
| HITL gate | agent proposes → human approves → mutation fires; agent proposes → timeout → MANUAL_REVIEW |
| Row-level ACL | ANALYST reads own claim (200), ANALYST reads other claim (403), ADMIN reads any (200) |
| Rate limit | under limit (200), at limit (200), over limit (429), window reset |
| Redis SCAN | ticket count with >100 active tickets (validates no KEYS * regression) |
| Error leakage | MCP internal error returns "upstream_internal_error", not raw SQL |
| Idempotency key | same actor+claim+action+reason → idempotent; same actor+claim+action+different reason → new write |
| MANUAL_REVIEW terminal | claim in MANUAL_REVIEW cannot transition to AWAITING_INPUT |

Integration tests hit a real PostgreSQL + Redis — no mocks for DB or Redis. Mocking the DB was identified as a root cause of prod regressions.

## Rule 6 — Secrets & Environment in CI
CI pipelines never hardcode secrets. All secrets (DB credentials, API keys, JWT secret, MCP shared secret, Langfuse encryption key) are stored in the CI/CD secret manager (GitHub Secrets, GitLab CI Variables, etc.) and injected as environment variables at runtime. `.env` files are never committed or uploaded to CI artifacts.

**Validation step:** CI runs a startup validation script that:
1. Asserts `JWT_SECRET_KEY` is not the placeholder `"dev-only-replace-me"` and is ≥ 32 characters.
2. Asserts `LANGFUSE_ENCRYPTION_KEY` is not `"0" * 64` and not empty.
3. Asserts `MCP_SHARED_SECRET` is not empty.
4. Asserts `CLAIM_UPLOAD_DIR` is not `/tmp` or a `/tmp` subdirectory.
5. Asserts `CORS_ALLOWED_ORIGINS` is a non-empty, non-wildcard value.

CI uses `.env.example` to validate that all required variables are declared before running tests. Any missing variable fails the pipeline immediately.

## Rule 7 — Docker Image Standards
Every Docker image is tagged with both the git commit SHA and a semantic version: `mcp-claims:1.2.3` and `mcp-claims:abc1234`. Images are built in CI, never locally pushed to a registry. Base images must be pinned to a specific digest — never use `latest` or a floating minor version tag. All images run as non-root users (`USER appuser` with `UID 1001`). Image size must be minimized — use multi-stage builds where applicable.

**Hardening requirements:**
- Production images must have a read-only root filesystem (`readOnlyRootFilesystem: true` in K8s pod spec) with explicit `emptyDir` mounts for writable directories.
- No image may run as `root` (UID 0). CI validates this by inspecting the built image manifest.
- `python:slim` without a pinned digest is forbidden as a base image. Use `python:3.12-slim@sha256:<digest>`.
- Upload directories (`CLAIM_UPLOAD_DIR`) must be mounted as persistent volumes in production — not `/tmp`, which is ephemeral per-pod.

## Rule 8 — Semantic Versioning
The project follows SemVer (`MAJOR.MINOR.PATCH`). `PATCH` — bug fixes, no API changes. `MINOR` — new features, backward compatible. `MAJOR` — breaking changes (schema changes, API contract changes, MCP tool signature changes). Version is tracked in a root `VERSION` file and referenced in `deployment/versioning.py`. A changelog entry is mandatory for every version bump. Security patches are always `PATCH` unless they require a breaking API change, in which case they are `MAJOR` with a migration guide.

## Rule 9 — CD Pipeline & Deployment Strategy
Merging to `main` triggers the CD pipeline automatically. Deployment strategy: **Blue/Green** for production — never deploy directly over the running instance. Rollback must be achievable in under 5 minutes by redeploying the previous Docker image. Kubernetes `deployment.yaml` files define `maxSurge: 1` and `maxUnavailable: 0` for zero-downtime deployments. No manual `kubectl apply` in production — all deployments go through the CD pipeline.

**Pre-deployment checks:**
- Startup validation (Rule 6 validation script) runs as a Kubernetes init container before the main container starts. If it fails, the deployment halts and the previous version remains active.
- Redis and PostgreSQL connectivity must be confirmed by the `/internal/ready` endpoint (real PING/query, not string checks) before traffic is routed to new pods.
- If the blue/green swap fails the readiness check within 3 minutes, the CD pipeline automatically rolls back to the previous blue environment.

## Rule 10 — Database Migration Standards
Schema changes are never applied manually to production. All migrations use Alembic. Migration files live in `migrations/` at the root. Every migration must have an `upgrade()` and a `downgrade()` function. Migrations are run as a pipeline step before the new version is deployed. Destructive migrations (DROP COLUMN, DROP TABLE) require a two-phase approach: deprecate first in one release, remove in the next.

**Required schema fields (never omit from migrations):**
- `claims.submitted_by_sub VARCHAR NOT NULL` — required for row-level ACL.
- `claims.decision_metadata JSONB NOT NULL DEFAULT '{}'` — required for audit/compliance.
- `claims.pdf_sha256 VARCHAR(64) NOT NULL` — SHA-256 of the uploaded PDF bytes; unique index on `(submitted_by_sub, pdf_sha256)` to enforce deduplication at the DB level as a second safety net.
- `claims.jti VARCHAR` index on the JWT blocklist table — required for O(1) revocation checks.

Alembic autogenerate must be reviewed manually before applying — autogenerate can miss JSONB constraints and custom triggers. The `updated_at` trigger must be re-applied after any table recreation migration.

## Rule 11 — Observability in CI/CD
Every deployment emits a deployment event to the observability layer (`observability/metrics.py`) with: service name, version, environment, timestamp, and deploying actor. Failed deployments trigger an alert immediately. LangSmith/LangFuse evaluation runs are triggered automatically after every production deployment to validate agent behavior has not regressed. Evaluation results are stored and compared against the previous deployment baseline.

**Security observability:**
- All JWT validation failures, revoked-token hits, rate-limit rejections, path-traversal attempts, and row-level ACL denials must be logged at `WARNING` level with the caller's `sub` and the endpoint. These are security signals and must flow to the central log aggregator.
- No `print()` in any file — including `observability/evaluation.py`, `observability/evals/_generate_fixtures.py`, and e2e test files. All output uses the structured logger from `observability/logging.py`.

## Rule 12 — Dependency Updates
Dependencies in `requirements.txt` are reviewed for updates on a monthly basis. Security vulnerability patches are applied immediately regardless of schedule — no exceptions. Dependency update PRs must pass the full CI pipeline (including `pip-audit`) before merging. Major version bumps of core dependencies (FastAPI, Pydantic, MCP SDK, python-jose/PyJWT) require a dedicated integration test run before merging to `main`.

**Supply-chain integrity:**
- Package-level `pyproject.toml` files must not introduce unpinned runtime dependencies. `pydantic>=2.0.0` is not acceptable; pin to `pydantic==X.Y.Z` or remove and inherit from root.
- `pip-audit` runs in CI on every PR. A PR that introduces a dependency with a known CVE is blocked from merging until the CVE is resolved or explicitly acknowledged with a documented exception.
- Hash-pinning (`--require-hashes` in `pip install`) is the target for production Docker builds to prevent supply-chain substitution attacks.
