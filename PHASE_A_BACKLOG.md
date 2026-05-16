# Phase A Backlog: Data Layer & Legacy API

**Phase Goal:** Establish the data foundation and create a Legacy Provider microservice to bridge Excel data with the modern database layer.

**Timeline:** Before Phase B (Intelligence & Agent Tooling)

---

## Epic 1: Database Setup & Migrations

### Task A1.1: Initialize Alembic Migration System
**Status:** Ready to start  
**Priority:** Critical (blocking all other tasks)  
**Points:** 3  
**Description:** Configure Alembic for PostgreSQL migrations.

**Acceptance Criteria:**
- [ ] Alembic initialized with `alembic init migrations`
- [ ] `migrations/env.py` connects to `DATABASE_URL` from `.env`
- [ ] `alembic.ini` configured for PostgreSQL
- [ ] Test migration runs successfully: `alembic revision --autogenerate -m "test"`
- [ ] `alembic upgrade head` works against local PostgreSQL
- [ ] `alembic downgrade -1` reverses migration cleanly

**Implementation Notes:**
- Use PostgreSQL 15+ (per schema comments)
- Follow Rule 10 (Operational Rules): Every migration must have `upgrade()` and `downgrade()`
- All sensitive data (DB credentials) comes from `.env`, never hardcoded
- Test against a local PostgreSQL instance before merging

**Tests:**
- [ ] Unit test: Migration upgrade succeeds
- [ ] Unit test: Migration downgrade succeeds
- [ ] Integration test: Schema matches expected structure after upgrade

---

### Task A1.2: Create Initial Schema Migration
**Status:** Waiting for A1.1  
**Priority:** Critical  
**Points:** 5  
**Description:** Translate the database schema SQL into an Alembic migration.

**Acceptance Criteria:**
- [ ] Migration file created: `migrations/versions/001_initial_schema.py`
- [ ] Migration creates all 3 tables: `users`, `policies`, `claims`
- [ ] Migration creates all ENUMs: `policy_status`, `severity_label`, `claim_status`
- [ ] Migration creates all indexes (5 total) as per schema
- [ ] Migration creates all triggers for `updated_at` management
- [ ] Migration runs successfully: `alembic upgrade head`
- [ ] Schema is verified against original SQL definition
- [ ] Downgrade migration works: `alembic downgrade base`

**Implementation Notes:**
- Copy SQL from database schema document
- Use Alembic's `op.execute()` for complex operations (ENUMs, triggers)
- Follow Rule 2 (AI Rules): Never hardcode monetary field types; use NUMERIC
- Include inline comments explaining non-obvious operations (e.g., trigger logic)

**Tests:**
- [ ] Integration test: Schema matches expected after migration
- [ ] Integration test: Constraints enforced (e.g., deductible < coverage_limit)
- [ ] Integration test: Triggers work (updated_at auto-updates)

---

## Epic 2: Legacy Data Migration

### Task A2.1: Create Excel Data Loader Script
**Status:** Waiting for A1.2  
**Priority:** High  
**Points:** 5  
**Description:** Build a Python script to parse `Legacy_Insure_Data.xlsx` and validate data.

**Acceptance Criteria:**
- [ ] Script reads Excel file from configurable path
- [ ] Script parses 3 sheets: `users`, `policies`, `claims`
- [ ] Script validates required fields per schema:
  - Users: `user_id`, `name`, `email`
  - Policies: `policy_id`, `user_id`, `type`, `status`, `deductible_usd`, `coverage_limit_usd`, `start_date`, `end_date`
  - Claims: `claim_id`, `user_id`, `policy_id`, `incident_date`, `incident_description`
- [ ] Script checks data types match database schema (e.g., dates are ISO 8601)
- [ ] Script detects and reports validation errors with line numbers
- [ ] Script outputs a validation report (CSV or JSON) before loading
- [ ] Script is idempotent (can run multiple times safely)

**Implementation Notes:**
- Use `pandas` for Excel parsing (already in requirements)
- Use Pydantic models from `packages/common/schemas/` for validation (Rule 11: Type Safety)
- No direct DB writes yet; only validation
- Store validation report in `data/validation_report.json` for audit trail
- Follow Rule 9 (Observability): All validation steps logged via `observability/logging.py`

**File Location:** `data/loaders/legacy_excel_loader.py`

**Tests:**
- [ ] Unit test: Parses valid Excel correctly
- [ ] Unit test: Detects missing required fields
- [ ] Unit test: Detects data type mismatches
- [ ] Unit test: Handles empty sheets gracefully
- [ ] Unit test: Idempotency check (run twice, same result)

---

### Task A2.2: Load Legacy Data into PostgreSQL
**Status:** Waiting for A2.1  
**Priority:** High  
**Points:** 5  
**Description:** Insert validated Excel data into the database.

**Acceptance Criteria:**
- [ ] Script connects to PostgreSQL using `DATABASE_URL` from `.env`
- [ ] Script loads users: inserts into `users` table
- [ ] Script loads policies: inserts into `policies` table with FK to users
- [ ] Script loads claims: inserts into `claims` table with FKs to users and policies
- [ ] Script respects database constraints:
  - No duplicate emails in users
  - Deductible < coverage_limit for policies
  - Policy start_date < end_date
  - Claim policy_id matches a policy owned by the user
- [ ] Script logs success/failure per record (not silent failures)
- [ ] Script outputs final count: "Loaded X users, Y policies, Z claims"
- [ ] Data can be verified via direct SQL query
- [ ] Script is idempotent (DELETE → INSERT pattern or UPSERT)

**Implementation Notes:**
- Use `sqlalchemy` ORM or psycopg2 for database operations
- Follow Rule 2 (AI Rules): Use `NUMERIC` for all monetary fields (no FLOAT rounding errors)
- Use transactions: If any row fails, rollback entire batch (atomic)
- All DB operations wrapped in error handling per Rule 11
- Log every operation via `observability/logging.py`
- Store pre-load and post-load row counts for audit

**File Location:** `data/loaders/postgres_loader.py`

**Tests:**
- [ ] Integration test: Data loads successfully against local PostgreSQL
- [ ] Integration test: FK constraints enforced (reject invalid policy_id)
- [ ] Integration test: Idempotency (run twice, data unchanged on second run)
- [ ] Integration test: Verify loaded data matches Excel source

---

## Epic 3: Legacy Provider Microservice

### Task A3.1: Create Legacy Provider MCP Structure
**Status:** Waiting for A2.2 (data must be loaded first)  
**Priority:** High  
**Points:** 3  
**Description:** Create the folder structure and placeholder for the Legacy Provider MCP.

**Acceptance Criteria:**
- [ ] Folder structure created: `mcp-servers/legacy-provider/`
  - `__init__.py`
  - `server.py` (placeholder)
  - `tools/` (folder)
  - `tests/` (unit & integration subfolders)
- [ ] MCP follows Rule 3: Owns exactly one domain (policy/user lookup)
- [ ] MCP does NOT import from other MCPs (Rule 3)
- [ ] MCP exports exactly 2 tools (see A3.2, A3.3)

**Implementation Notes:**
- MCP name: `legacy_provider` (read-only domain, no writes)
- Follow monorepo structure (Rule 1): All shared schemas from `packages/common/`
- Include docstrings explaining purpose and boundaries

**Tests:**
- [ ] Unit test: MCP server can be instantiated
- [ ] Unit test: Tool registry is correct (2 tools)

---

### Task A3.2: Implement "Get Policy by User ID" Tool
**Status:** Waiting for A3.1  
**Priority:** Critical  
**Points:** 5  
**Description:** Create MCP tool to query policies for a given user.

**Tool Name:** `get_policies_by_user_id`

**Signature:**
```python
def get_policies_by_user_id(user_id: int) -> List[PolicySchema]
```

**Acceptance Criteria:**
- [ ] Tool accepts `user_id` as integer parameter
- [ ] Tool queries PostgreSQL directly (read-only)
- [ ] Tool returns list of `PolicySchema` objects (from `packages/common/schemas/policy.py`)
- [ ] Tool returns empty list if user has no policies
- [ ] Tool returns 404 error if user doesn't exist
- [ ] Tool includes all policy fields: `policy_id`, `type`, `status`, `deductible_usd`, `coverage_limit_usd`, `start_date`, `end_date`
- [ ] Tool follows Rule 11: Type hints on all parameters and return types
- [ ] Tool wrapped in error handling (Rule 11)
- [ ] All queries logged via `observability/logging.py` (Rule 9)
- [ ] Response time < 500ms (performance requirement)

**Implementation Notes:**
- Use SQLAlchemy ORM (not raw SQL) for safety
- Implement index usage: `idx_policies_user_id` should be hit by this query
- Return only ACTIVE policies by default (filter by status)
- Option: Include filter parameter for `status` to allow auditors to see expired policies

**File Location:** `mcp-servers/legacy-provider/tools/get_policies_by_user_id.py`

**Tests:**
- [ ] Unit test: Returns correct policies for valid user_id
- [ ] Unit test: Returns empty list for user with no policies
- [ ] Unit test: Raises 404 for non-existent user
- [ ] Integration test: Query performance on 1000+ records
- [ ] Integration test: Pydantic validation of return type

---

### Task A3.3: Implement "Get User by ID" Tool
**Status:** Waiting for A3.1  
**Priority:** High  
**Points:** 3  
**Description:** Create MCP tool to query user details.

**Tool Name:** `get_user_by_id`

**Signature:**
```python
def get_user_by_id(user_id: int) -> UserSchema
```

**Acceptance Criteria:**
- [ ] Tool accepts `user_id` as integer parameter
- [ ] Tool queries PostgreSQL (read-only)
- [ ] Tool returns `UserSchema` object with all fields: `user_id`, `name`, `email`, `created_at`, `updated_at`
- [ ] Tool raises 404 error if user doesn't exist
- [ ] Tool follows Rule 11: Type hints, error handling
- [ ] All queries logged via `observability/logging.py` (Rule 9)
- [ ] Response time < 100ms

**Implementation Notes:**
- Straightforward query: `SELECT * FROM users WHERE user_id = ?`
- Use `idx_users_pk` (primary key index)
- No sensitive data exposed (no passwords, tokens, etc. — n/a for this schema)

**File Location:** `mcp-servers/legacy-provider/tools/get_user_by_id.py`

**Tests:**
- [ ] Unit test: Returns correct user for valid user_id
- [ ] Unit test: Raises 404 for non-existent user
- [ ] Integration test: Query performance

---

### Task A3.4: Create Legacy Provider Server Entry Point
**Status:** Waiting for A3.2, A3.3  
**Priority:** High  
**Points:** 3  
**Description:** Implement the MCP server that exposes both tools.

**Acceptance Criteria:**
- [ ] Server file: `mcp-servers/legacy-provider/server.py`
- [ ] Server registers 2 tools: `get_user_by_id`, `get_policies_by_user_id`
- [ ] Server initializes on startup (connects to PostgreSQL)
- [ ] Server exposes tools via MCP protocol (follows mcp-py-sdk)
- [ ] Server follows Rule 9: Uses `observability/tracers/base_tracer.py` for all traces
- [ ] Server logs startup/shutdown events
- [ ] Server handles connection errors gracefully

**Implementation Notes:**
- Use mcp-py-sdk for MCP implementation
- Connection pool: Use SQLAlchemy engine with connection pooling (Rule 2: DB Integrity)
- Startup logging should include: service name, port, environment
- Follow Rule 7: Auth is enforced at `mcp_router.py` level (MCP itself is internal)

**File Location:** `mcp-servers/legacy-provider/server.py`

**Tests:**
- [ ] Integration test: Server starts and registers tools
- [ ] Integration test: Server can execute both tools
- [ ] Integration test: Error handling (connection failure, invalid input)

---

### Task A3.5: Create Docker & Kubernetes Configs for Legacy Provider
**Status:** Waiting for A3.4  
**Priority:** Medium  
**Points:** 3  
**Description:** Add containerization for the legacy provider MCP.

**Acceptance Criteria:**
- [ ] Dockerfile created: `infra/docker/Dockerfile.mcp-legacy-provider`
- [ ] Dockerfile follows Rule 10: Multi-stage build, non-root user, pinned base image
- [ ] K8s deployment created: `infra/k8s/mcp-legacy-provider/deployment.yaml`
- [ ] K8s service created: `infra/k8s/mcp-legacy-provider/service.yaml`
- [ ] Service exposes port 8006 (or configured via `MCP_LEGACY_PROVIDER_PORT` in `.env`)
- [ ] Deployment includes resource limits (CPU, memory)
- [ ] Health check configured (liveness probe)

**Implementation Notes:**
- Base image: `python:3.11-slim`
- Image size target: < 500MB
- Non-root user: `mcp` (UID 1000)
- Health endpoint: GET `/health` returns 200 OK

**Files:**
- `infra/docker/Dockerfile.mcp-legacy-provider`
- `infra/k8s/mcp-legacy-provider/deployment.yaml`
- `infra/k8s/mcp-legacy-provider/service.yaml`

**Tests:**
- [ ] Docker build succeeds
- [ ] Docker image runs locally: `docker run -e DATABASE_URL=... -p 8006:8006 ...`
- [ ] K8s manifests are valid: `kubectl apply --dry-run ...`

---

## Epic 4: Testing & Verification

### Task A4.1: Write Unit Tests for Data Layer
**Status:** Waiting for A2.1, A2.2  
**Priority:** High  
**Points:** 5  
**Description:** Create comprehensive unit tests for data loaders.

**Acceptance Criteria:**
- [ ] Test file: `data/loaders/tests/unit/test_excel_loader.py`
- [ ] Coverage >= 80% for `legacy_excel_loader.py`
- [ ] Tests cover:
  - Valid Excel parsing
  - Missing field detection
  - Data type validation
  - Empty sheet handling
  - Idempotency check
- [ ] Test file: `data/loaders/tests/unit/test_postgres_loader.py`
- [ ] Coverage >= 80% for `postgres_loader.py`
- [ ] Tests cover:
  - Successful insert
  - FK constraint violation
  - Duplicate email rejection
  - Transaction rollback on error
- [ ] All tests pass: `pytest data/loaders/tests/unit/ -v`

**Implementation Notes:**
- Use `pytest` for testing framework
- Use `pytest-mock` for mocking database connections
- Use temporary in-memory SQLite for integration-style unit tests
- Follow Rule 5 (Operational Rules): Every test file named `test_<module>.py`

---

### Task A4.2: Write Integration Tests for Legacy Provider MCP
**Status:** Waiting for A3.4  
**Priority:** High  
**Points:** 5  
**Description:** Create end-to-end tests for the legacy provider microservice.

**Acceptance Criteria:**
- [ ] Test file: `mcp-servers/legacy-provider/tests/integration/test_legacy_provider.py`
- [ ] Tests against local PostgreSQL instance
- [ ] Test: `get_user_by_id(101)` returns Juan Pérez
- [ ] Test: `get_policies_by_user_id(101)` returns correct policies
- [ ] Test: Error handling (non-existent user, invalid input)
- [ ] Test: Performance (response time < 500ms)
- [ ] All tests pass: `pytest mcp-servers/legacy-provider/tests/integration/ -v`

**Implementation Notes:**
- Use `pytest` with PostgreSQL fixture (per Rule 4, Operational)
- Load test data via migration before each test
- Clean up test data after each test
- Mock external calls if any (Rule 11: structured error handling)

---

### Task A4.3: Verify Data Integrity
**Status:** Waiting for A2.2  
**Priority:** High  
**Points:** 3  
**Description:** Manual verification that loaded data matches source.

**Acceptance Criteria:**
- [ ] Run SQL validation queries (provided below)
- [ ] Row counts match: users, policies, claims
- [ ] No orphaned FKs (policies without users, claims without policies)
- [ ] All monetary fields are NUMERIC, not FLOAT
- [ ] Sample data point verified: User ID 101 (Juan Pérez) has correct policies and claims
- [ ] Documentation created: `data/VERIFICATION_REPORT.md`

**Validation Queries:**
```sql
-- Count loaded data
SELECT COUNT(*) as user_count FROM users;
SELECT COUNT(*) as policy_count FROM policies;
SELECT COUNT(*) as claim_count FROM claims;

-- Check for orphaned FKs
SELECT p.policy_id FROM policies p
  LEFT JOIN users u ON p.user_id = u.user_id
  WHERE u.user_id IS NULL;

SELECT c.claim_id FROM claims c
  LEFT JOIN policies p ON c.policy_id = p.policy_id
  WHERE p.policy_id IS NULL;

-- Verify test user JP01
SELECT * FROM users WHERE user_id = 101;
SELECT * FROM policies WHERE user_id = 101;
SELECT * FROM claims WHERE user_id = 101;
```

---

## Summary Timeline

```
Week 1:
  - A1.1: Alembic setup
  - A1.2: Initial schema migration
  - A2.1: Excel loader script
  - A2.2: PostgreSQL data load
  ↓
Week 2:
  - A3.1: Legacy Provider MCP structure
  - A3.2: get_policies_by_user_id tool
  - A3.3: get_user_by_id tool
  - A3.4: MCP server entry point
  ↓
Week 3:
  - A3.5: Docker & K8s configs
  - A4.1: Unit tests
  - A4.2: Integration tests
  - A4.3: Data verification
  ↓
Phase A Complete ✓
```

---

## Definition of Done for Phase A

Phase A is **complete** when:
- [ ] All tasks marked DONE
- [ ] All tests passing: `pytest data/ mcp-servers/legacy-provider/ -v`
- [ ] Data verified: All rows match source Excel
- [ ] Docker image builds and runs: `docker run ...` succeeds
- [ ] K8s manifests valid and deployable
- [ ] MCP tools callable and return expected data
- [ ] Code review approved (at least 1 engineer)
- [ ] No CI/CD pipeline errors
- [ ] Changelog updated with Phase A summary

---

## Blockers & Dependencies

```
A1.1 (Alembic) → BLOCKS → A1.2 (Schema Migration)
                            ↓
A1.2 → BLOCKS → A2.1 (Excel Loader) → BLOCKS → A2.2 (PostgreSQL Load)
                                                   ↓
A2.2 → BLOCKS → A3.1, A3.2, A3.3, A4.1, A4.2, A4.3
                   ↓
A3.1 → BLOCKS → A3.2, A3.3, A3.4
                   ↓
A3.2, A3.3 → BLOCKS → A3.4
                       ↓
A3.4 → BLOCKS → A3.5, A4.2
```

**Critical Path:** A1.1 → A1.2 → A2.1 → A2.2 → Phase A Complete

---

## Notes for Implementation

1. **Sample Test Data:** Use Juan Pérez (ID: 101) claim report from `Claim_Report_JP01.pdf` as reference for expected data
2. **Environment Setup:** Ensure `.env` is set up with PostgreSQL credentials before starting
3. **Database Reset:** To reset for testing: `alembic downgrade base && alembic upgrade head`
4. **Logging:** All operations logged; check `logs/` directory for detailed records
5. **Review Points:** Schedule code review after A2.2 and A3.4 (major milestones)
