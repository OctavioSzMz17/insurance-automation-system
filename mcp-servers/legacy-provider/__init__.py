"""Legacy Provider MCP Server package.

Exposes legacy insurance data (users + policies) loaded from Excel
into PostgreSQL as read-only MCP tools for use by AI agents.

Domain: legacy-data (read-only)
Tools:  get_user_by_id | get_policies_by_user_id
"""
