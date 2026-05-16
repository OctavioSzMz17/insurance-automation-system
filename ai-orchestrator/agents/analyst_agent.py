"""Analyst Agent — Extraction & Triage.

Responsible for:
1. Extracting claim information from PDF reports
2. Parsing structured data (dates, amounts, locations)
3. Assessing severity (LOW, HIGH, TOTAL_LOSS)
4. Initial reasoning about coverage

Implements Rule 4: Agent Responsibilities - READ-ONLY, extraction-focused
Follows ReAct pattern: think → act (use tools) → observe → reflect
"""

# TODO: Implement analyst_agent using Claude API with tools:
#
# Tools (via MCP):
# - documents.extract_text_from_pdf(file_path)
# - documents.extract_entities_from_document(text, document_type="claim_report")
# - policies.get_policy_by_user_id(user_id)
# - claims.create_claim_draft(...)
#
# Output: ClaimDraft with:
# - incident_date, incident_description, incident_location
# - severity_label (LOW | HIGH | TOTAL_LOSS)
# - extracted_damage_amount
# - ai_reasoning (chain-of-thought)
