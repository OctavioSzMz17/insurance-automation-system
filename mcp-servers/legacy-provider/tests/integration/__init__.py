"""Integration tests for the Legacy Provider MCP server.

Tests in this package hit a real PostgreSQL instance (Neon dev schema)
and verify end-to-end tool behaviour:
    - get_user_by_id returns correct row for seeded user
    - get_user_by_id raises ValueError for unknown ID
    - get_policies_by_user_id returns all policies for a seeded user
    - get_policies_by_user_id returns [] for a user with no policies

Implemented in A4.2.
"""
