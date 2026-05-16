"""Main API Orchestrator.

FastAPI application that receives claims and orchestrates multi-agent validation.
Returns claim status: APPROVED, REJECTED, or MANUAL_REVIEW.

Implements Rule 7: Security (JWT auth, CORS)
Implements Rule 11: Type Safety with Pydantic validation
"""

from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field

# TODO: Implement endpoints:
#
# POST /claims/submit
#   - Accept PDF file + user_id
#   - Extract claim data via documents MCP
#   - Route to analyst_agent for triage
#   - Return claim_id
#
# GET /claims/{claim_id}
#   - Return current claim status and reasoning
#
# POST /claims/{claim_id}/approve
#   - Auditor action: approve claim
#   - Requires JWT with role: auditor
#
# POST /claims/{claim_id}/reject
#   - Auditor action: reject claim with reason
#   - Requires JWT with role: auditor


class ClaimSubmitRequest(BaseModel):
    """Request model for claim submission."""
    user_id: int = Field(..., description="ID of user filing the claim")
    incident_date: datetime = Field(..., description="Date of incident")
    incident_description: str = Field(..., description="Description of the incident")
    incident_location: Optional[str] = Field(None, description="Location of incident")
    # file: UploadFile for PDF


class ClaimStatusResponse(BaseModel):
    """Response model for claim status."""
    claim_id: int
    user_id: int
    policy_id: int
    claim_status: str  # APPROVED, REJECTED, MANUAL_REVIEW, AWAITING_INPUT
    severity_label: Optional[str]  # LOW, HIGH, TOTAL_LOSS
    ai_reasoning: str
    created_at: datetime
    updated_at: datetime


# TODO: Implement FastAPI app, routes, JWT middleware
