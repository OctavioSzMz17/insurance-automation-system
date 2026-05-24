# Local Docker Dev Environment (C3.1)

One-command stack for working on the orchestrator without installing Python
deps on your host. All env vars come from the project-root `.env` (gitignored).

## Two modes

### Default — orchestrator only, talks to your Neon DB

Uses whatever `DATABASE_URL` you have in `.env` (typically Neon).

```bash
docker compose -f infra/docker/docker-compose.yml up --build
```

Then hit it:

```bash
curl http://localhost:8000/health        # liveness
curl http://localhost:8000/ready         # readiness (checks LLM key + DB url)
curl http://localhost:8000/docs          # Swagger UI
```

### Mode 2 — full offline stack with local Postgres

Spins up a Postgres container + runs Alembic migrations + then the orchestrator.

```bash
docker compose -f infra/docker/docker-compose.yml \
    --profile local-db up --build
```

While this mode is running, temporarily point your `.env` to:

```
DATABASE_URL=postgresql://insureai:dev@postgres:5432/insureai
```

Note the host is `postgres` (the compose service name), not `localhost` — that's
how containers reach each other on the `insureai-dev` network.

## Stopping things

```bash
# Stop containers, keep the postgres volume
docker compose -f infra/docker/docker-compose.yml down

# Stop + wipe the Postgres volume (lose all local data)
docker compose -f infra/docker/docker-compose.yml down -v
```

## Services in `docker-compose.yml`

| Service          | Container port | Host-published? | Profile    | Purpose                                       |
|------------------|----------------|-----------------|------------|-----------------------------------------------|
| `orchestrator`   | 8000           | yes (8000)      | (default)  | FastAPI app                                   |
| `mcp-claims`     | 8002           | no (expose)     | (default)  | Claims MCP server — SSE                       |
| `mcp-policies`   | 8003           | no (expose)     | (default)  | Policies MCP server — SSE                     |
| `mcp-users`      | 8004           | no (expose)     | (default)  | Users MCP server — SSE                        |
| `mcp-documents`  | 8005           | no (expose)     | (default)  | Documents MCP server — SSE (PDF extraction)   |
| `mcp-legacy`     | 8006           | no (expose)     | (default)  | Legacy-provider MCP server — SSE              |
| `postgres`       | 5432           | yes (5432)      | `local-db` | Postgres 16 with `insureai:dev` user          |
| `migrations`     | —              | —               | `local-db` | One-shot Alembic `upgrade head`               |

All MCP services communicate over SSE (Rule 7) and are reachable **only on
the `insureai-dev` docker network** — `expose` (no host port). Every
inbound request is authenticated by `MCPAuthMiddleware`, which validates
the shared `X-MCP-Secret` header against `MCP_SHARED_SECRET` in `.env`
(Master Patch Part 1).

For local development without Docker, the MCP servers bind to
`localhost:8002-8006` and the orchestrator's `MCP_*_URL` defaults point
there. You still need `MCP_SHARED_SECRET` set in `.env`.

## Common gotchas

- **`.env` not found** — the compose file mounts `../../.env`. Run from the
  project root, not from inside `infra/docker/`.
- **`MCP_SHARED_SECRET must be set`** — every MCP server fails fast at
  startup if the secret is missing or shorter than 32 chars. Generate one:
  `openssl rand -hex 32` and put it in `.env`.
- **Port 8000 already in use** — only the orchestrator publishes a host
  port now. Kill the conflicting process or change `API_PORT` in `.env`
  and the matching mapping in `docker-compose.yml`.
- **Build is slow the first time** — that's expected. Cached after the first build.
