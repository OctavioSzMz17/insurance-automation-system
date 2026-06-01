"""Narrow view of orchestrator config that runtime modules need.

We pass this small protocol around instead of the full OrchestratorSettings so
unit tests can mock the surface in 5 lines.
"""
from __future__ import annotations

from typing import Protocol


class OrchestratorRuntimeView(Protocol):
    mcp_claims_url: str
    mcp_policies_url: str
    mcp_users_url: str
    mcp_documents_url: str
    mcp_shared_secret: str
    hmac_proposal_key: str
    analyst_max_steps: int
    analyst_timeout_seconds: int
    analyst_proposal_ttl_seconds: int
    llm_model: str
    anthropic_api_key: str
    # mTLS client cert paths (empty string = TLS disabled)
    mcp_client_cert: str
    mcp_client_key: str
    mcp_ca_cert: str
