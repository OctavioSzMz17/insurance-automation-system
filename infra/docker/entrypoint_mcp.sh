#!/usr/bin/env sh
# entrypoint_mcp.sh — Start a MCP server with optional TLS.
#
# Env vars (all optional — omit for plain HTTP):
#   MCP_TLS_CERT  path to server certificate (PEM)
#   MCP_TLS_KEY   path to server private key (PEM)
#   MCP_CA_CERT   path to CA cert; when set, client certs are required (mTLS)
#
# Required env vars (injected by docker-compose / k8s):
#   MCP_APP       ASGI app path, e.g. "claims.server:app"
#   MCP_PORT      port to bind, e.g. "8001"
set -eu

APP="${MCP_APP:?MCP_APP is required}"
PORT="${MCP_PORT:?MCP_PORT is required}"

ARGS="$APP --host 0.0.0.0 --port $PORT --app-dir /app/mcp-servers"

if [ -n "${MCP_TLS_CERT:-}" ] && [ -n "${MCP_TLS_KEY:-}" ]; then
    ARGS="$ARGS --ssl-certfile $MCP_TLS_CERT --ssl-keyfile $MCP_TLS_KEY"
    if [ -n "${MCP_CA_CERT:-}" ]; then
        # ssl-cert-reqs=2 → ssl.CERT_REQUIRED (require client cert)
        ARGS="$ARGS --ssl-ca-certs $MCP_CA_CERT --ssl-cert-reqs 2"
    fi
    echo "[entrypoint] TLS enabled for $APP on port $PORT"
else
    echo "[entrypoint] Plain HTTP for $APP on port $PORT"
fi

exec uvicorn $ARGS
