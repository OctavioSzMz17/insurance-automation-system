"""Documents MCP server. Read-only PDF inspection inside CLAIM_UPLOAD_DIR (Rule 8, 13).

Defenses (defense-in-depth):
1. pipeline_id is a UUID validated by Pydantic — strings like '../etc/passwd' never reach disk.
2. Filename is always derived server-side: f"{pipeline_id}.pdf" — the caller cannot influence it.
3. Resolved path must satisfy `is_relative_to(UPLOAD_DIR.resolve())` — blocks symlinks/escapes.
4. The path must point at a *regular* file (no FIFOs, devices, symlinks).
5. CLAIM_UPLOAD_DIR is forbidden under /tmp in protected envs (validated at startup).
"""
