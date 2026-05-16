"""Unit tests for Legacy Provider tools.

Tests in this package mock the database and verify:
    - Correct SQL is built
    - Correct serialisation (Decimal → str, date → ISO string)
    - ValueError raised when user not found
    - Empty list returned when user has no policies

Implemented in A4.1.
"""
