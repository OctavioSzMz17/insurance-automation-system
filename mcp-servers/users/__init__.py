"""Users MCP server. Read-only in Phase 1 (Rule 3, Rule 4).

There is intentionally NO `list_all_users` tool for ANALYST callers. Enumeration
of users is an IDOR risk and Rule 7 forbids it. ADMIN-only listing, if needed,
will be a dedicated tool in Phase 2.
"""
