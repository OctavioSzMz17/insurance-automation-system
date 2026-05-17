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

| Service        | Profile     | Purpose                                    |
|----------------|-------------|--------------------------------------------|
| `orchestrator` | (default)   | FastAPI app on port 8000                   |
| `postgres`     | `local-db`  | Postgres 16 with `insureai:dev` user       |
| `migrations`   | `local-db`  | One-shot Alembic `upgrade head`            |

## Common gotchas

- **`.env` not found** — the compose file mounts `../../.env`. Run from the
  project root, not from inside `infra/docker/`.
- **Port 8000 already in use** — kill whatever's running on it
  (`netstat -ano | grep 8000` on Windows), or change `API_PORT` in `.env`
  AND the port mapping in `docker-compose.yml`.
- **Build is slow the first time** — that's expected. Cached after the first build.
- **Image is ~600 MB** — the orchestrator embeds all MCP source. Will shrink
  once the MCPs switch to standalone SSE services.
