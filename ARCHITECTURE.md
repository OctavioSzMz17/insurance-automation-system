# Insurance Automation System — Architecture & Scaffolding Guide

This document describes the project structure, the rules that guide development, and what's been scaffolded vs. what needs implementation.

## Project Vision

**Goal**: Build a multi-agent insurance claim triage system that reduces manual claim processing from 48 hours to seconds.

**Business Problem**: InsureAI needs to automate the following workflow:
1. Customer submits claim report (PDF)
2. System extracts claim data (using LLM + OCR)
3. Analyst agent triages claim and assesses severity
4. Auditor agent validates coverage against policy clauses
5. System returns decision: APPROVED, REJECTED, or MANUAL_REVIEW

---

## Directory Structure

```
insurance-automation-system/
│
├── .github/workflows/              # CI/CD pipelines
│   ├── lint-and-test.yml          # Linting, type check, unit tests, integration tests
│   └── deploy.yml                  # Build Docker images, Blue/Green deployment
│
├── .agents/rules/                  # Development guidelines (FOLLOW THESE!)
│   ├── ai-rules.md                 # Rules 1-12: Code structure, agents, MCP, security
│   └── operational-rules.md        # Rules 1-12: Git, CI/CD, testing, deployment
│
├── ai-orchestrator/                # Main orchestration layer
│   ├── agents/
│   │   ├── analyst_agent.py        # [TODO] Extraction & triage agent (analyst)
│   │   ├── auditor_agent.py        # [TODO] Review & decision agent (auditor)
│   │   └── reasoning/
│   │       └── coverage_rules.py   # [TODO] Coverage matching logic
│   │
│   ├── routes/
│   │   ├── agent_router.py         # Routes between agents
│   │   ├── mcp_router.py           # Routes to MCP servers
│   │   └── validation_pipeline.py  # [EXISTS] Validates agent output
│   │
│   ├── validation/                 # [TEMPLATE] Self-healing validation
│   │   ├── __init__.py
│   │   └── pipeline.py             # [TODO] Retry logic, escalation
│   │
│   └── tests/
│       ├── unit/                   # [TEMPLATE] Agent unit tests
│       │   ├── test_analyst_agent.py
│       │   └── test_auditor_agent.py
│       └── integration/            # [TEMPLATE] End-to-end flow tests
│           └── test_claim_triage_flow.py
│
├── mcp-servers/                    # Model Context Protocol servers (data & tools)
│   │
│   ├── claims/                     # Claims MCP
│   │   ├── server.py               # [TODO] Implement claims tools
│   │   └── tests/
│   │
│   ├── policies/                   # Policies MCP
│   │   ├── server.py               # [TODO] Implement policies tools
│   │   └── tests/
│   │
│   ├── users/                      # Users MCP
│   │   ├── server.py               # [TODO] Implement users tools
│   │   └── tests/
│   │
│   ├── documents/                  # [NEW] Document extraction MCP
│   │   ├── server.py               # [TODO] PDF parsing, OCR, entity extraction
│   │   ├── tools/
│   │   │   ├── extract_text.py     # [TODO] PDF → text extraction
│   │   │   ├── extract_entities.py # [TODO] Text → structured data
│   │   │   └── validate_pdf.py     # [TODO] PDF validation
│   │   └── tests/
│   │       ├── unit/
│   │       └── integration/
│   │
│   └── config/
│       ├── config.yaml             # Service configuration
│       └── secrets.yaml            # Secrets references (NO raw secrets!)
│
├── packages/common/                # Shared code (Rule 1: Monorepo)
│   ├── schemas/
│   │   ├── claim.py                # Claim data model
│   │   ├── policy.py               # Policy data model
│   │   └── user.py                 # User data model
│   ├── enums.py                    # Shared enums (policy_status, claim_status, etc.)
│   └── exceptions.py               # Shared exception classes
│
├── deployment/                     # Deployment & API
│   ├── api.py                      # [TODO] FastAPI app, endpoints
│   ├── scaling.py                  # Scaling configuration
│   └── versioning.py               # Version management
│
├── observability/                  # Monitoring & tracing
│   ├── logging.py                  # Central logger (Rule 9)
│   ├── metrics.py                  # Metrics collection
│   ├── evaluation.py               # Agent evaluation
│   └── tracers/
│       ├── base_tracer.py          # Base tracer interface
│       ├── langsmith_tracer.py     # LangSmith integration
│       └── langfuse_tracer.py      # Langfuse integration
│
├── pipelines/                      # Data pipelines
│   ├── pipeline_definitions.py     # Pipeline orchestration
│   └── postprocessing.py           # Post-claim processing
│
├── infra/                          # Infrastructure as Code
│   ├── docker/
│   │   ├── Dockerfile.orchestrator # [EXISTS] Orchestrator image
│   │   ├── Dockerfile.mcp-*        # [EXISTS] MCP images
│   │   ├── Dockerfile.mcp-documents# [NEW] Document extraction image
│   │   └── docker-compose.yml      # Local development (Rule 10)
│   │
│   └── k8s/
│       ├── namespace.yaml          # Kubernetes namespace
│       ├── configmaps/
│       │   └── app-config.yaml     # ConfigMaps
│       ├── orchestrator/
│       │   ├── deployment.yaml
│       │   └── service.yaml
│       ├── mcp-*/
│       │   ├── deployment.yaml
│       │   └── service.yaml
│       └── mcp-documents/          # [NEW] Document MCP deployment
│           ├── deployment.yaml
│           └── service.yaml
│
├── migrations/                     # [TEMPLATE] Database migrations (Alembic)
│   ├── env.py                      # Alembic environment
│   ├── script.py.mako              # Migration template
│   └── versions/                   # Migration files
│
├── .env.example                    # [NEW] Template env vars
├── .gitignore                      # [NEW] Git ignore patterns
├── alembic.ini                     # [NEW] Alembic configuration
├── VERSION                         # [NEW] Semantic version (0.1.0)
├── CHANGELOG.md                    # [NEW] Release notes
├── pyproject.toml                  # [NEW] Python project config
├── ruff.toml                       # [NEW] Linter config
├── mypy.ini                        # [NEW] Type checker config
│
└── requirements.txt                # All dependencies (pinned versions)
```

---

## What's Been Scaffolded (✅)

1. **Directory Structure** — All directories exist with proper `__init__.py` files
2. **Configuration Files**:
   - `.env.example` — Environment variables template
   - `pyproject.toml` — Python project metadata
   - `ruff.toml` — Linter rules
   - `mypy.ini` — Type checker settings
   - `alembic.ini` — Database migration tool
   - `.gitignore` — Git exclusions
3. **CI/CD Pipelines**:
   - `.github/workflows/lint-and-test.yml` — Lint, type check, test
   - `.github/workflows/deploy.yml` — Blue/Green deployment
4. **Database Migrations**:
   - `migrations/env.py` — Alembic environment
   - `migrations/script.py.mako` — Migration template
5. **Infrastructure**:
   - `infra/docker/Dockerfile.mcp-documents` — New MCP image
   - `infra/k8s/mcp-documents/deployment.yaml` — K8s deployment
   - `infra/k8s/mcp-documents/service.yaml` — K8s service
6. **Validation Pipeline**:
   - `ai-orchestrator/validation/pipeline.py` — Skeleton with TODO comments
7. **Test Templates**:
   - `ai-orchestrator/tests/unit/test_*.py` — Unit test stubs
   - `ai-orchestrator/tests/integration/test_claim_triage_flow.py` — Integration test stub
   - `mcp-servers/documents/tests/*/test_*.py` — Document MCP test stubs
8. **Documentation**:
   - `VERSION` — Semantic version tracking
   - `CHANGELOG.md` — Release notes template
   - `ARCHITECTURE.md` — This file

---

## What Needs Implementation (🚀)

### Phase A: Data Layer & PDF Extraction

**Timeline**: Start here

1. **Load Legacy Data** (low effort)
   - Read `Legacy_Insure_Data.xlsx` (referenced in Technical_Challenge_InsureAI.pdf)
   - Create data loader script to populate PostgreSQL
   - Verify users and policies are in the database

2. **Implement Document Extraction MCP** (high effort)
   - `mcp-servers/documents/server.py` — Main MCP server
   - `mcp-servers/documents/tools/extract_text.py` — PDF → text (pypdf or pdfplumber)
   - `mcp-servers/documents/tools/extract_entities.py` — Text → structured data (entity extraction)
   - `mcp-servers/documents/tools/validate_pdf.py` — Validation (size, format, corruption check)
   - Tools must return structured JSON matching `packages/common/schemas`

3. **Implement MCP Servers for Claims/Policies/Users** (medium effort)
   - Each server needs tools to:
     - **Claims MCP**: `create_claim()`, `get_claim()`, `update_claim_status()`, `list_claims_by_user()`
     - **Policies MCP**: `get_policy_by_id()`, `get_policy_by_user_id()`, `get_coverage_clauses()`
     - **Users MCP**: `get_user_by_id()`, `get_user_email()`
   - All tools must use PostgreSQL (via SQLAlchemy or raw queries)
   - Mark write operations with `@write_restricted` decorator

### Phase B: Agent Intelligence

**Timeline**: After Phase A

1. **Implement Analyst Agent** (`ai-orchestrator/agents/analyst_agent.py`)
   - Tool calls:
     - `documents.extract_text_from_pdf()`
     - `documents.extract_entities()`
     - `users.get_user_by_id()`
     - `policies.get_policy_by_user_id()`
   - Output: ClaimDraft with `severity_label` (LOW, HIGH, TOTAL_LOSS)
   - Reasoning stored in `ai_reasoning` field
   - Follows ReAct pattern (think → act → observe → reflect)

2. **Implement Auditor Agent** (`ai-orchestrator/agents/auditor_agent.py`)
   - Tool calls:
     - `policies.get_policy_details()`
     - `policies.get_coverage_clauses()` 
     - `claims.update_claim_status()` [WRITE]
   - Logic: Compare policy clauses with incident facts
   - Output: Final decision (APPROVED, REJECTED, MANUAL_REVIEW)
   - Reasoning stored in `ai_reasoning` for auditors

3. **Implement Coverage Reasoning** (`ai-orchestrator/agents/reasoning/coverage_rules.py`)
   - Rule engine for policy matching
   - Handle edge cases → escalate to MANUAL_REVIEW
   - Example: "Does policy cover collision with fixed object?" → Check policy clauses

### Phase C: Orchestration & API

**Timeline**: After Phase B

1. **Implement Validation Pipeline** (`ai-orchestrator/validation/pipeline.py`)
   - `validate_agent_output()` — Check claim_status is valid enum, amounts are non-negative, etc.
   - `retry_on_constraint_violation()` — Retry agent max 3 times if validation fails
   - `escalate_to_manual_review()` — Move to MANUAL_REVIEW after 3 failures
   - All retries logged with timestamps

2. **Implement FastAPI Application** (`deployment/api.py`)
   - Endpoints:
     - `POST /claims/submit` — Accept PDF, route to analyst
     - `GET /claims/{claim_id}` — Get claim status
     - `POST /claims/{claim_id}/approve` — Auditor action
     - `POST /claims/{claim_id}/reject` — Auditor action
   - JWT authentication (Rule 7)
   - CORS configuration from `.env`
   - All responses use Pydantic models

3. **Implement Database Loader**
   - Script to load `Legacy_Insure_Data.xlsx` into PostgreSQL
   - Create users, policies, sample claims
   - Verify referential integrity

### Phase D: Testing & Deployment

**Timeline**: Parallel with Phase B/C

1. **Write Tests** (every new function needs a test)
   - Unit tests in `ai-orchestrator/tests/unit/`
   - Integration tests in `ai-orchestrator/tests/integration/`
   - Minimum 80% coverage (Rule 5)

2. **Verify CI/CD**
   - GitHub Actions pipelines should pass
   - Linting (ruff), type checking (mypy), tests (pytest)
   - Docker builds succeed
   - Database migrations run cleanly

3. **Manual Testing with Samples**
   - Use `Claim_Report_JP01.pdf` to test end-to-end
   - Expected: System identifies covered incident → APPROVED
   - Audit trail should show reasoning at each step

---

## Key Rules to Remember During Implementation

### Code Structure (AI Rules)
- **Rule 1**: All shared schemas in `packages/common/` — never duplicate
- **Rule 2**: PostgreSQL only, NUMERIC for money, never FLOAT
- **Rule 3**: Each MCP owns one domain, no cross-imports
- **Rule 4**: Analyst → READ-ONLY; Auditor → READ + WRITE; both follow ReAct
- **Rule 5**: Validation pipeline retries 3x, then escalates to MANUAL_REVIEW
- **Rule 11**: Type hints mandatory, Pydantic validation before processing

### Operations (Operational Rules)
- **Rule 1**: Trunk-based development, short-lived feature branches
- **Rule 2**: Conventional Commits (`feat(scope):`, `fix(scope):`)
- **Rule 4**: CI pipeline: lint → type check → unit tests → integration tests → build
- **Rule 5**: Tests required for every new function; 80% coverage minimum
- **Rule 6**: Secrets in CI/CD secret manager, never hardcoded
- **Rule 10**: Database migrations using Alembic; must have `upgrade()` and `downgrade()`

---

## Testing Strategy (Rule 5)

```python
# Unit test example: test_analyst_agent.py
def test_analyst_extracts_claim_data():
    # Arrange: Mock PDF extraction
    mock_pdf_text = "Juan Pérez... collided with light pole..."
    
    # Act: Call analyst agent
    result = analyst_agent(pdf_text=mock_pdf_text)
    
    # Assert: Check output structure and reasoning
    assert result.severity_label == "HIGH"
    assert result.ai_reasoning is not None
    assert len(result.ai_reasoning) > 0

# Integration test example: test_claim_triage_flow.py
def test_end_to_end_approved_claim():
    # 1. Load JP01 PDF from test fixtures
    # 2. Submit via POST /claims/submit
    # 3. Check analyst processes it
    # 4. Check auditor approves
    # 5. Assert claim_status == "APPROVED"
```

---

## Next Steps

1. **Read `.agents/rules/ai-rules.md` and `operational-rules.md`** — These guide every decision
2. **Start with Phase A**: Load legacy data, implement document extraction MCP
3. **Test each component** as you build (Rule 5 in operational)
4. **Use the templates** in this architecture as starting points
5. **Follow the commit format** (`feat(mcp-documents): add PDF extraction`)

---

## Resources

- Technical Challenge: `Technical_Challenge_InsureAI.pdf`
- Sample Claim: `Claim_Report_JP01.pdf`
- Database Schema: Review PostgreSQL schema in project root
- MCP Documentation: See `.agents/rules/` for tool patterns
- Python Standards: `pyproject.toml` + `ruff.toml` + `mypy.ini`
