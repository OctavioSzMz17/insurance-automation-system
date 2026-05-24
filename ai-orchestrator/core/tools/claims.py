"""Claims tools bundle for agent consumption.

create_claim is intentionally NOT exposed here (Rule 6 / HITL gate).
DB persistence is the orchestrator FastAPI handler's responsibility —
the agent only produces a TriageProposal (reasoning + recommendation).

CLAIMS_TOOLS is kept as an empty list so imports in __init__.py stay valid
and no agent accidentally gains write access.
"""

CLAIMS_TOOLS: list = []
