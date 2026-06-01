"""Shared utilities for MCP servers: transport, auth, errors, db, redis, logging.

NOT a domain. Domain MCPs (claims, policies, users, documents) must NOT import
business logic from here — only infrastructure helpers (Rule 3).
"""
