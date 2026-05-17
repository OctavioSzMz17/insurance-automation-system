"""End-to-end tests — Phase C4.

These hit a REAL LLM, REAL Neon DB, and the REAL FastAPI app (via
TestClient). They cost actual money per run, so they are OPT-IN:

    RUN_E2E=1 py -m pytest ai-orchestrator/tests/e2e -m e2e -v

Without `RUN_E2E=1` they are skipped — including by CI.
"""
