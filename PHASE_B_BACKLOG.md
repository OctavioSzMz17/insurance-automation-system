# Phase B Backlog: Intelligence & Agent Tooling

**Phase Goal:** Implement AI agents that can reason about insurance coverage and make triage decisions.

**Timeline:** After Phase A (requires data layer)  
**Depends on:** Phase A completion (Legacy Provider MCP must be working)

---

## Epic 1: Document Extraction & Data Parsing

### Task B1.1: Implement PDF Text Extraction Tool
**Status:** Waiting for Phase A  
**Priority:** Critical  
**Points:** 5  
**Description:** Create MCP tool to extract text from PDF claim reports.

**Tool Name:** `extract_text_from_pdf`

**Signature:**
```python
def extract_text_from_pdf(file_path: str, page_range: Optional[Tuple[int, int]] = None) -> str
```

**Acceptance Criteria:**
- [ ] Accepts PDF file path (local or uploaded)
- [ ] Returns extracted text as string
- [ ] Handles multi-page PDFs (optional page range parameter)
- [ ] Gracefully handles scanned PDFs (returns empty or triggers OCR)
- [ ] Validates file type: only `.pdf` allowed
- [ ] Validates file size: max 50MB (from `.env` PDF_MAX_SIZE_MB)
- [ ] Handles corrupted PDFs with clear error message
- [ ] Follows Rule 11: Type hints, error handling, logging
- [ ] Response time < 5 seconds for typical claim report

**Implementation Notes:**
- Use `pypdf` or `pdfplumber` for text extraction
- For scanned PDFs: Use `pytesseract` + `Pillow` for OCR (if OCR_ENABLED=true in .env)
- Store extracted text temporarily in memory (no disk writes)
- Log: filename, page count, character count, extraction method (text vs OCR)

**File Location:** `mcp-servers/documents/tools/extract_text_from_pdf.py`

**Tests:**
- [ ] Unit test: Extracts text from valid PDF
- [ ] Unit test: Rejects invalid file types
- [ ] Unit test: Handles multi-page PDF
- [ ] Unit test: Rejects oversized files
- [ ] Integration test: Handles sample `Claim_Report_JP01.pdf`

---

### Task B1.2: Implement Entity Extraction Tool
**Status:** Waiting for B1.1  
**Priority:** Critical  
**Points:** 8  
**Description:** Extract structured entities from claim report text (dates, locations, damage descriptions).

**Tool Name:** `extract_entities_from_claim_text`

**Signature:**
```python
def extract_entities_from_claim_text(text: str) -> ClaimEntitySchema
```

**ClaimEntitySchema** (from `packages/common/schemas/claim.py`):
```python
class ClaimEntitySchema(BaseModel):
    incident_date: Optional[date]
    incident_location: Optional[str]
    incident_description: str
    reported_damage_types: List[str]
    reported_damage_amount: Optional[Decimal]
    claimant_statement: Optional[str]
    extraction_confidence: Dict[str, float]  # per field confidence
```

**Acceptance Criteria:**
- [ ] Extracts incident_date (ISO 8601 format)
- [ ] Extracts incident_location (city, region)
- [ ] Extracts incident_description (verbatim)
- [ ] Extracts damage types (e.g., ["front bumper", "headlight", "radiator"])
- [ ] Extracts reported_damage_amount (numeric, currency-agnostic)
- [ ] Follows Rule 11: Returns Pydantic model with all fields
- [ ] Includes confidence scores for each extracted field
- [ ] Handles missing fields gracefully (returns None, not errors)
- [ ] All extractions logged with confidence metrics

**Implementation Notes:**
- Use Claude API with structured extraction prompt
- Prompt strategy: Few-shot examples with sample claims
- Hallucination prevention: Only extract facts mentioned in text; no inference
- Confidence scores: 0.0-1.0 indicating extraction certainty
- If confidence < 0.5 on critical field, mark as uncertain in output

**File Location:** `mcp-servers/documents/tools/extract_entities_from_claim_text.py`

**Tests:**
- [ ] Unit test: Extracts all fields from sample JP01 claim
- [ ] Unit test: Handles missing fields without errors
- [ ] Unit test: Rejects invalid date formats
- [ ] Unit test: Confidence scores are within 0.0-1.0 range
- [ ] Integration test: End-to-end with actual PDF extraction

---

### Task B1.3: Create Documents MCP Server Entry Point
**Status:** Waiting for B1.2  
**Priority:** High  
**Points:** 3  
**Description:** Implement the MCP server that exposes document extraction tools.

**Acceptance Criteria:**
- [ ] Server file: `mcp-servers/documents/server.py`
- [ ] Server registers 2 tools: `extract_text_from_pdf`, `extract_entities_from_claim_text`
- [ ] Server initializes on startup
- [ ] Server follows Rule 7: Auth enforced at mcp_router level
- [ ] Server follows Rule 9: Uses `observability/tracers/base_tracer.py`
- [ ] Startup/shutdown events logged

**File Location:** `mcp-servers/documents/server.py`

**Tests:**
- [ ] Integration test: Server starts and registers tools
- [ ] Integration test: Tools callable and return Pydantic models

---

### Task B1.4: Create Docker & Kubernetes for Documents MCP
**Status:** Waiting for B1.3  
**Priority:** Medium  
**Points:** 3  
**Description:** Containerize the documents extraction microservice.

**Acceptance Criteria:**
- [ ] Dockerfile: `infra/docker/Dockerfile.mcp-documents`
- [ ] K8s deployment: `infra/k8s/mcp-documents/deployment.yaml`
- [ ] K8s service: `infra/k8s/mcp-documents/service.yaml`
- [ ] Service exposes port 8005
- [ ] Image includes OCR dependencies (tesseract) if OCR_ENABLED=true
- [ ] Resource limits set (CPU, memory)
- [ ] Health check configured

**Files:**
- `infra/docker/Dockerfile.mcp-documents`
- `infra/k8s/mcp-documents/deployment.yaml`
- `infra/k8s/mcp-documents/service.yaml`

**Tests:**
- [ ] Docker build succeeds
- [ ] Docker run: `docker run -p 8005:8005 ...`
- [ ] K8s manifests valid

---

## Epic 2: Coverage Reasoning & Decision Logic

### Task B2.1: Define Coverage Rules Engine
**Status:** Waiting for Phase A  
**Priority:** Critical  
**Points:** 5  
**Description:** Create a structured representation of insurance coverage rules.

**Description:**
Insurance policies have clauses that define what is covered and what is not. Example from JP01:
- "Collision against fixed object" → Covered if policy includes comprehensive coverage
- "Act of God" → May be covered under specific conditions
- "Driver negligence" → May be excluded

This task creates a rules engine to represent these clauses programmatically.

**Acceptance Criteria:**
- [ ] Coverage rules defined in: `ai-orchestrator/agents/reasoning/coverage_rules.py`
- [ ] Rules structure: 
  ```python
  COVERAGE_RULES = {
      "comprehensive": {
          "collision_fixed_object": True,
          "collision_vehicle": True,
          "theft": True,
          "act_of_god": False,
      },
      "liability_only": {
          "collision_fixed_object": False,
          ...
      }
  }
  ```
- [ ] Rules accessible via function: `is_damage_covered(policy_type: str, damage_type: str) -> bool`
- [ ] Function includes audit trail: returns reason (str) for decision
- [ ] Follows Rule 11: Type hints, error handling

**Implementation Notes:**
- Rules can be sourced from actual insurance policy documents
- For MVP: Hardcode 3-4 policy types (comprehensive, liability, basic, premium)
- For future: Load from database (create policy_clauses table)
- Include comments explaining business logic

**File Location:** `ai-orchestrator/agents/reasoning/coverage_rules.py`

**Tests:**
- [ ] Unit test: Returns True for covered damages
- [ ] Unit test: Returns False for excluded damages
- [ ] Unit test: Returns reason explanation

---

### Task B2.2: Create Analyst Agent Decision Logic
**Status:** Waiting for B1.2, B2.1  
**Priority:** Critical  
**Points:** 8  
**Description:** Implement the analyst agent that triages claims.

**Responsibility (from Rule 4):**
- READ-ONLY access to MCPs (Legacy Provider, Documents)
- Extract incident details from claim report
- Evaluate severity (LOW, HIGH, TOTAL_LOSS)
- Initial reasoning about coverage (no final decision)
- Output: Structured triage summary

**Signature:**
```python
async def analyze_claim(
    user_id: int,
    pdf_file_path: str
) -> AnalystOutputSchema
```

**AnalystOutputSchema:**
```python
class AnalystOutputSchema(BaseModel):
    claim_id: Optional[int]
    user_id: int
    incident_date: date
    incident_description: str
    incident_location: Optional[str]
    extracted_damage_types: List[str]
    reported_damage_amount: Optional[Decimal]
    severity_label: str  # "LOW", "HIGH", "TOTAL_LOSS"
    preliminary_coverage_assessment: str  # "Likely Covered", "Likely Excluded", "Uncertain"
    analyst_reasoning: str  # Detailed explanation for auditor
    confidence_score: float  # 0.0-1.0
```

**Acceptance Criteria:**
- [ ] Agent reads claim PDF via Documents MCP (`extract_text_from_pdf`)
- [ ] Agent extracts entities via Documents MCP (`extract_entities_from_claim_text`)
- [ ] Agent retrieves policy via Legacy Provider MCP (`get_policies_by_user_id`)
- [ ] Agent evaluates severity based on damage description
- [ ] Agent uses coverage_rules to assess preliminary coverage
- [ ] Agent generates detailed reasoning (for auditor review)
- [ ] Agent returns AnalystOutputSchema (Pydantic model)
- [ ] Follows Rule 4: READ-ONLY; no database writes
- [ ] Follows Rule 4: ReAct pattern (think → act → observe → reflect)
- [ ] All reasoning logged via `observability/logging.py`

**Implementation Notes:**
- Use Claude API with structured prompt + tools
- Prompt template: Include examples of claim triage (few-shot learning)
- Tools available: extract_text, extract_entities, get_policies (via MCP router)
- ReAct example:
  ```
  Thought: Need to extract claim details and check policy
  Action: extract_entities_from_claim_text(text)
  Observation: incident_date=2026-05-05, damage=["bumper", "headlight"]
  Action: get_policies_by_user_id(101)
  Observation: policy_type="comprehensive"
  Thought: Collision against fixed object is covered under comprehensive
  Final Answer: preliminary_coverage = "Likely Covered"
  ```

**File Location:** `ai-orchestrator/agents/analyst_agent.py`

**Tests:**
- [ ] Unit test: Parses sample JP01 claim correctly
- [ ] Unit test: Severity classification (LOW/HIGH/TOTAL_LOSS)
- [ ] Unit test: Preliminary coverage assessment matches policy
- [ ] Integration test: End-to-end with actual MCPs (Legacy Provider, Documents)

---

### Task B2.3: Create Auditor Agent Decision Logic
**Status:** Waiting for B2.2, B2.1  
**Priority:** Critical  
**Points:** 8  
**Description:** Implement the auditor agent that makes final claim decisions.

**Responsibility (from Rule 4):**
- READ + restricted WRITE access to Claims MCP
- Reviews analyst triage output
- Applies strict coverage matching
- Makes final decision: APPROVED, REJECTED, MANUAL_REVIEW
- Output: Final claim status + decision reason

**Signature:**
```python
async def audit_and_decide(
    analyst_output: AnalystOutputSchema,
    policy_id: int
) -> AuditorOutputSchema
```

**AuditorOutputSchema:**
```python
class AuditorOutputSchema(BaseModel):
    claim_status: str  # "APPROVED", "REJECTED", "MANUAL_REVIEW"
    decision_reason: str
    coverage_matching_details: Dict[str, bool]  # per damage type coverage
    recommended_payout: Optional[Decimal]
    auditor_reasoning: str
    confidence_score: float
```

**Acceptance Criteria:**
- [ ] Agent reviews analyst_output (READ)
- [ ] Agent retrieves policy details via Legacy Provider MCP
- [ ] Agent matches each damage type to policy coverage
- [ ] Agent applies strict matching: ALL damages must be covered for APPROVED
- [ ] Agent decides:
  - APPROVED: All damages covered AND confidence > 0.8
  - REJECTED: Any damage excluded OR confidence < 0.5
  - MANUAL_REVIEW: Uncertain cases (confidence 0.5-0.8)
- [ ] Agent outputs AuditorOutputSchema
- [ ] Follows Rule 4: ReAct pattern for decision making
- [ ] Follows Rule 4: @write_restricted decorator on write operations
- [ ] All reasoning logged

**Implementation Notes:**
- Decision matrix:
  ```
  All covered + high confidence → APPROVED
  Any excluded → REJECTED
  Uncertain coverage or low confidence → MANUAL_REVIEW
  ```
- Coverage matching example:
  ```
  Damage: "front bumper" → Policy covers "collision_fixed_object"? Yes
  Damage: "LED headlight" → Policy covers collision? Yes
  Damage: "radiator damage" → Policy covers collision? Yes
  Result: All covered → APPROVED (if high confidence)
  ```

**File Location:** `ai-orchestrator/agents/auditor_agent.py`

**Tests:**
- [ ] Unit test: APPROVED decision for fully covered claim
- [ ] Unit test: REJECTED decision for excluded damage
- [ ] Unit test: MANUAL_REVIEW for uncertain case
- [ ] Integration test: End-to-end with analyst output

---

## Epic 3: Agent Integration & Validation

### Task B3.1: Implement Validation Pipeline (from Phase A scaffolding)
**Status:** Waiting for B2.3  
**Priority:** High  
**Points:** 5  
**Description:** Implement self-healing validation pipeline per Rule 5.

**Acceptance Criteria:**
- [ ] Function: `validate_agent_output()` validates analyst and auditor outputs
- [ ] Function: `retry_on_constraint_violation()` retries agent on validation failure
- [ ] Function: `escalate_to_manual_review()` escalates after 3 retries
- [ ] Retry logic: Agent receives error feedback, tries again
- [ ] Max 3 attempts per claim
- [ ] After 3 failures: Automatically escalate to MANUAL_REVIEW
- [ ] All retry attempts logged with timestamps

**Implementation Notes:**
- Constraints to validate:
  - Analyst output has all required fields
  - Auditor output is one of (APPROVED, REJECTED, MANUAL_REVIEW)
  - Decision reason is not empty
  - Confidence score 0.0-1.0
- Error feedback to agent: "Field X failed constraint Y. Details: Z. Please retry."

**File Location:** `ai-orchestrator/validation/pipeline.py`

**Tests:**
- [ ] Unit test: Validates correct output (passes)
- [ ] Unit test: Catches invalid status (fails)
- [ ] Unit test: Retry logic (3 attempts max)
- [ ] Integration test: Escalation to MANUAL_REVIEW

---

### Task B3.2: Create Agent Router & Orchestration
**Status:** Waiting for B3.1  
**Priority:** High  
**Points:** 5  
**Description:** Route claims through analyst → validation → auditor pipeline.

**Signature:**
```python
async def triage_claim(
    user_id: int,
    claim_pdf_path: str,
    policy_id: int
) -> ClaimDecisionSchema
```

**Acceptance Criteria:**
- [ ] Step 1: Call analyst_agent with claim PDF
- [ ] Step 2: Validate analyst output; retry if needed
- [ ] Step 3: Call auditor_agent with analyst output
- [ ] Step 4: Validate auditor output; retry if needed
- [ ] Step 5: Return final decision
- [ ] Each step logged with timestamp
- [ ] Error handling: Any agent failure → escalate to MANUAL_REVIEW

**Implementation Notes:**
- Pipeline is sequential (analyst first, then auditor)
- If analyst fails after 3 retries: Still proceed to auditor with partial data
- If auditor fails after 3 retries: Return MANUAL_REVIEW
- Follow Rule 4: agents only via MCP router; no direct calls

**File Location:** `ai-orchestrator/routes/agent_router.py` (update existing)

**Tests:**
- [ ] Integration test: Full pipeline with happy path
- [ ] Integration test: Analyst failure handling
- [ ] Integration test: Auditor failure handling

---

## Epic 4: Testing & Verification

### Task B4.1: Unit Tests for Coverage Rules
**Status:** Waiting for B2.1  
**Priority:** Medium  
**Points:** 3  
**Description:** Test coverage rules engine.

**Acceptance Criteria:**
- [ ] Test all policy types (comprehensive, liability, etc.)
- [ ] Test all damage types
- [ ] Coverage >= 90%

---

### Task B4.2: Integration Tests for Agents
**Status:** Waiting for B3.2  
**Priority:** High  
**Points:** 8  
**Description:** End-to-end tests for analyst and auditor agents.

**Test Cases:**
- [ ] Test JP01 claim: Car hit pole (comprehensive policy) → APPROVED
- [ ] Test excluded damage claim → REJECTED
- [ ] Test uncertain claim → MANUAL_REVIEW

**Tests:**
- [ ] Integration test: JP01 happy path → APPROVED
- [ ] Integration test: Excluded coverage → REJECTED
- [ ] Integration test: Low confidence → MANUAL_REVIEW

---

## Summary Timeline

```
Week 4-5:
  - B1.1: PDF text extraction
  - B1.2: Entity extraction
  - B1.3: Documents MCP server
  ↓
Week 6:
  - B1.4: Docker & K8s for Documents
  - B2.1: Coverage rules engine
  - B2.2: Analyst agent
  ↓
Week 7:
  - B2.3: Auditor agent
  - B3.1: Validation pipeline
  ↓
Week 8:
  - B3.2: Agent router
  - B4.1, B4.2: Tests
  ↓
Phase B Complete ✓
```

---

## Definition of Done for Phase B

Phase B is **complete** when:
- [ ] All tasks marked DONE
- [ ] Analyst agent successfully parses JP01 claim
- [ ] Auditor agent makes decision (APPROVED/REJECTED/MANUAL_REVIEW)
- [ ] Validation pipeline retries and escalates correctly
- [ ] All tests passing (>80% coverage)
- [ ] Code review approved
- [ ] No CI/CD errors
- [ ] Ready for Phase C (Orchestrator API)
