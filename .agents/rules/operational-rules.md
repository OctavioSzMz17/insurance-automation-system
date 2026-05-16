---
trigger: always_on
---

# Operational Rules — Insurance Automation System

## Rule 1 — Git Branching Strategy
Follow trunk-based development with short-lived feature branches. Branch naming convention: `feature/<ticket-id>-short-description`, `fix/<ticket-id>-short-description`, `hotfix/<ticket-id>-short-description`. No direct commits to `main` — ever. All changes go through a Pull Request with at least one peer review approval. `main` is always deployable.

## Rule 2 — Commit Standards
All commits follow Conventional Commits format: `type(scope): description`. Valid types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`. Scope maps to the service changed: `feat(mcp-claims)`, `fix(orchestrator)`, `test(common)`. Commit messages are written in English, present tense, imperative mood. No commit may bundle unrelated changes — one concern per commit.

## Rule 3 — Pull Request Standards
Every PR must include: a description of what changed and why, reference to the ticket/issue, and a testing summary. PRs that touch `packages/common/` require review from at least two engineers — changes there affect the entire monorepo. PRs must pass all CI checks before merging. No self-merging.

## Rule 4 — CI Pipeline (Per Service)
Every push to a feature branch triggers the CI pipeline automatically. Pipeline stages in order:
1. **Lint** — `ruff` or `flake8` must pass with zero warnings
2. **Type Check** — `mypy` must pass with zero errors
3. **Unit Tests** — must pass with minimum 80% coverage
4. **Integration Tests** — must pass against a local PostgreSQL instance
5. **Build** — Docker image must build successfully

All stages must pass. A single failure blocks the PR from merging.

## Rule 5 — Testing Standards
Unit tests live in `mcp-servers/*/tests/unit/` and mirror the structure of the service they test. Integration tests live in `mcp-servers/*/tests/integration/`. Agent behavior tests live in `ai-orchestrator/tests/`. Test files are named `test_<module_name>.py`. Every new function or MCP tool must have a corresponding unit test. Every new agent action must have an integration test covering at least: happy path, invalid input, and constraint violation.

## Rule 6 — Secrets & Environment in CI
CI pipelines never hardcode secrets. All secrets (DB credentials, API keys, JWT secret) are stored in the CI/CD secret manager (GitHub Secrets, GitLab CI Variables, etc.) and injected as environment variables at runtime. `.env` files are never committed or uploaded to CI artifacts. The CI pipeline uses `.env.example` to validate that all required variables are declared before running tests.

## Rule 7 — Docker Image Standards
Every Docker image is tagged with both the git commit SHA and a semantic version: `mcp-claims:1.2.3` and `mcp-claims:abc1234`. Images are built in CI, never locally pushed to a registry. Base images must be pinned to a specific version — never use `latest`. All images run as non-root users. Image size must be minimized — use multi-stage builds where applicable.

## Rule 8 — Semantic Versioning
The project follows SemVer (`MAJOR.MINOR.PATCH`). `PATCH` — bug fixes, no API changes. `MINOR` — new features, backward compatible. `MAJOR` — breaking changes (schema changes, API contract changes, MCP tool signature changes). Version is tracked in a root `VERSION` file and referenced in `deployment/versioning.py`. A changelog entry is mandatory for every version bump.

## Rule 9 — CD Pipeline & Deployment Strategy
Merging to `main` triggers the CD pipeline automatically. Deployment strategy: **Blue/Green** for production — never deploy directly over the running instance. Rollback must be achievable in under 5 minutes by redeploying the previous Docker image. Kubernetes `deployment.yaml` files define `maxSurge: 1` and `maxUnavailable: 0` for zero-downtime deployments. No manual `kubectl apply` in production — all deployments go through the CD pipeline.

## Rule 10 — Database Migration Standards
Schema changes are never applied manually to production. All migrations use a migration tool (Alembic). Migration files live in a `migrations/` directory at the root. Every migration must have an `upgrade()` and a `downgrade()` function. Migrations are run as a pipeline step before the new version is deployed. Destructive migrations (DROP COLUMN, DROP TABLE) require a two-phase approach: deprecate first in one release, remove in the next.

## Rule 11 — Observability in CI/CD
Every deployment emits a deployment event to the observability layer (`observability/metrics.py`) with: service name, version, environment, timestamp, and deploying actor. Failed deployments trigger an alert immediately. LangSmith/LangFuse evaluation runs are triggered automatically after every production deployment to validate agent behavior has not regressed. Evaluation results are stored and compared against the previous deployment baseline.

## Rule 12 — Dependency Updates
Dependencies in `requirements.txt` are reviewed for updates on a monthly basis. Security vulnerability patches are applied immediately regardless of schedule — no exceptions. Dependency update PRs must pass the full CI pipeline before merging. Major version bumps of core dependencies (FastAPI, Pydantic, MCP SDK) require a dedicated integration test run before merging to `main`.