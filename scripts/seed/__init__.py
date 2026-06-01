"""Seed data for local development and integration tests.

UUIDs are deterministic (uuid5 over a fixed namespace) so tests can hardcode references.
The script is idempotent: re-running does not duplicate rows (UPSERT by stable key).
"""
