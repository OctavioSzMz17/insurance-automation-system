#!/usr/bin/env bash
# gen_dev_certs.sh — Generate a self-signed CA + per-service certs for local mTLS dev.
#
# Output layout (all under infra/certs/dev/):
#   ca.key / ca.crt              — Certificate Authority (never leave your machine)
#   orchestrator.key / .crt      — Orchestrator client cert (presented to MCPs)
#   claims.key    / claims.crt   — claims MCP server cert
#   policies.key  / policies.crt — policies MCP server cert
#   users.key     / users.crt    — users MCP server cert
#   documents.key / documents.crt— documents MCP server cert
#
# Usage:
#   bash scripts/certs/gen_dev_certs.sh
#   # Or from WSL / Git Bash on Windows
#
# Requirements: openssl 1.1.1+
# All certs are valid for 825 days (Safari/Chrome max for local dev).
# DO NOT use these certs in staging or production.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT_DIR="$REPO_ROOT/infra/certs/dev"
mkdir -p "$OUT_DIR"

DAYS=825
SUBJ_PREFIX="/C=US/ST=Dev/L=Dev/O=InsuranceDev"

# ── Helper: generate cert signed by CA ───────────────────────────────────────
# Usage: sign_cert <name> <cn> <san_extras>
# san_extras: comma-separated additional SANs beyond "DNS:localhost,IP:127.0.0.1"
sign_cert() {
    local name="$1"
    local cn="$2"
    local extra_sans="${3:-}"

    local sans="DNS:localhost,DNS:${cn},IP:127.0.0.1"
    if [[ -n "$extra_sans" ]]; then
        sans="${sans},${extra_sans}"
    fi

    openssl genrsa -out "$OUT_DIR/${name}.key" 2048 2>/dev/null
    openssl req -new -key "$OUT_DIR/${name}.key" \
        -subj "${SUBJ_PREFIX}/CN=${cn}" \
        -out  "$OUT_DIR/${name}.csr"
    openssl x509 -req \
        -in "$OUT_DIR/${name}.csr" \
        -CA "$OUT_DIR/ca.crt" -CAkey "$OUT_DIR/ca.key" -CAcreateserial \
        -days "$DAYS" -sha256 \
        -extfile <(printf "subjectAltName=%s\nextendedKeyUsage=serverAuth,clientAuth" "$sans") \
        -out "$OUT_DIR/${name}.crt" 2>/dev/null
    rm -f "$OUT_DIR/${name}.csr"
    echo "  ✓ $name"
}

echo "=== Generating dev mTLS certs in $OUT_DIR ==="

# ── 1. Certificate Authority ──────────────────────────────────────────────────
echo "→ CA"
openssl genrsa -out "$OUT_DIR/ca.key" 4096 2>/dev/null
openssl req -new -x509 \
    -key  "$OUT_DIR/ca.key" \
    -subj "${SUBJ_PREFIX}/CN=InsuranceDevCA" \
    -days "$DAYS" -sha256 \
    -out  "$OUT_DIR/ca.crt" 2>/dev/null
echo "  ✓ ca"

# ── 2. Orchestrator client cert ───────────────────────────────────────────────
echo "→ Orchestrator (client)"
openssl genrsa -out "$OUT_DIR/orchestrator.key" 2048 2>/dev/null
openssl req -new -key "$OUT_DIR/orchestrator.key" \
    -subj "${SUBJ_PREFIX}/CN=orchestrator" \
    -out  "$OUT_DIR/orchestrator.csr"
openssl x509 -req \
    -in "$OUT_DIR/orchestrator.csr" \
    -CA "$OUT_DIR/ca.crt" -CAkey "$OUT_DIR/ca.key" -CAcreateserial \
    -days "$DAYS" -sha256 \
    -extfile <(printf "extendedKeyUsage=clientAuth") \
    -out "$OUT_DIR/orchestrator.crt" 2>/dev/null
rm -f "$OUT_DIR/orchestrator.csr"
echo "  ✓ orchestrator"

# ── 3. MCP server certs ───────────────────────────────────────────────────────
echo "→ MCP servers"
sign_cert "claims"    "claims"    "DNS:claims"
sign_cert "policies"  "policies"  "DNS:policies"
sign_cert "users"     "users"     "DNS:users"
sign_cert "documents" "documents" "DNS:documents"

# ── 4. Permissions ────────────────────────────────────────────────────────────
chmod 600 "$OUT_DIR"/*.key
chmod 644 "$OUT_DIR"/*.crt

echo ""
echo "Done! Files written to: $OUT_DIR"
echo ""
echo "Next steps:"
echo "  1. Add these vars to your .env (adjust paths as needed):"
echo "     MCP_CLIENT_CERT=/path/to/infra/certs/dev/orchestrator.crt"
echo "     MCP_CLIENT_KEY=/path/to/infra/certs/dev/orchestrator.key"
echo "     MCP_CA_CERT=/path/to/infra/certs/dev/ca.crt"
echo "     MCP_TLS_CERT=/path/to/infra/certs/dev/claims.crt  (per MCP)"
echo "     MCP_TLS_KEY=/path/to/infra/certs/dev/claims.key   (per MCP)"
echo "  2. Change MCP_CLAIMS_URL etc. from http:// to https://"
echo ""
echo "WARNING: These are DEV-ONLY self-signed certs. Never use in production."
