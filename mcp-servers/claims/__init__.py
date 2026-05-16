"""Claims MCP Server package.

Owns the claim-write domain. Exposes write-restricted tools that the
auditor_agent uses to persist its decisions to PostgreSQL.

Domain: claims (READ + restricted WRITE)
Tools:  create_claim  (@write_restricted)
"""
