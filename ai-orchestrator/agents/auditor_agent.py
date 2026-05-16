"""Auditor Agent — Review & Decision.

Responsible for:
1. Reviewing analyst's extraction and triage
2. Comparing policy clauses with incident facts
3. Validating coverage eligibility
4. Making final decision: APPROVED | REJECTED | MANUAL_REVIEW

Implements Rule 4: Agent Responsibilities - READ + restricted WRITE
Follows ReAct pattern with constraint validation
"""

# TODO: Implement auditor_agent using Claude API with tools:
#
# Tools (via MCP):
# - policies.get_policy_details(policy_id)
# - policies.get_policy_coverage_clauses(policy_id)
# - claims.update_claim_status(...) [WRITE, requires @write_restricted decorator]
#
# Logic:
# 1. Get policy coverage clauses
# 2. Match incident type to coverage
# 3. Check deductibles and limits
# 4. Make coverage decision
# 5. Handle edge cases → MANUAL_REVIEW
#
# Output: ClaimDecision with:
# - claim_status (APPROVED | REJECTED | MANUAL_REVIEW)
# - ai_reasoning (detailed justification)
# - coverage_analysis
