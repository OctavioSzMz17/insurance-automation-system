# Self-hosted Langfuse

## Why this is not in our `docker-compose.yml`

Langfuse 4 self-hosted is **six services** — `langfuse-web`, `langfuse-worker`,
Postgres, ClickHouse, Redis and MinIO — wired with roughly eighty environment
variables. Vendoring that into [`docker-compose.yml`](../../docker-compose.yml) would
triple the file, bury the four MCP servers that are the actual subject of this
repository, and leave us maintaining a copy of upstream's deployment topology.

Rule 10 also requires images pinned to exact digests. Upstream's compose uses floating
tags (`langfuse:4`, `clickhouse-server:25.12`), so merging it as-is would violate our own
rule, and inventing digests is not something to do from memory.

So we run upstream's compose as upstream publishes it, and keep only the wiring here.

> **Not verified end to end.** These instructions were written from upstream's
> `docker-compose.yml` and configuration reference, not from a run in this repository.
> The orchestrator side *is* verified — see
> `ai-orchestrator/tests/unit/test_langfuse_tracer.py`, which drives the real SDK
> offline. Treat the stack below as a recipe to validate, not as a tested path.

## Bring it up

```bash
mkdir -p infra/observability/vendor && cd infra/observability/vendor
curl -O https://raw.githubusercontent.com/langfuse/langfuse/main/docker-compose.yml
```

Generate the three secrets it needs:

```bash
openssl rand -hex 32      # ENCRYPTION_KEY — must be 64 hex chars
openssl rand -base64 32   # SALT
openssl rand -base64 32   # NEXTAUTH_SECRET
```

Put them in our `.env` as `LANGFUSE_ENCRYPTION_KEY`, `LANGFUSE_SALT` and
`LANGFUSE_NEXTAUTH_SECRET`, then pass them through to upstream's compose (it reads
`ENCRYPTION_KEY`, `SALT`, `NEXTAUTH_SECRET`). `config.cross_validate()` refuses to boot a
production orchestrator whose `LANGFUSE_ENCRYPTION_KEY` is 64 zeros (Rule 8) — that
placeholder would leave every secret in the Langfuse database encrypted with a published
value.

```bash
docker compose -f infra/observability/vendor/docker-compose.yml up -d
```

## Get API keys without clicking through the UI

Upstream's `langfuse-web` accepts `LANGFUSE_INIT_*` variables that provision an
organization, a project, a user **and the project's API keys** on first boot. That is
what makes this reproducible from `.env` instead of a manual setup nobody can repeat:

```bash
LANGFUSE_INIT_ORG_ID=insurance
LANGFUSE_INIT_ORG_NAME=Insurance Automation
LANGFUSE_INIT_PROJECT_ID=lia
LANGFUSE_INIT_PROJECT_NAME=LIA claims pipeline
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=pk-lf-...     # you choose these
LANGFUSE_INIT_PROJECT_SECRET_KEY=sk-lf-...
LANGFUSE_INIT_USER_EMAIL=you@example.com
LANGFUSE_INIT_USER_NAME=You
LANGFUSE_INIT_USER_PASSWORD=...
```

Set the same pair in our `.env` as `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`.

## Point the orchestrator at it

```bash
OBSERVABILITY_BACKEND=log,langfuse   # keep local log visibility as well
OBSERVABILITY_MASK=pii
LANGFUSE_BASE_URL=http://localhost:3000
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
```

Composing `log,langfuse` is deliberate: when a trace is missing from the UI you want to
know whether it was never emitted or never delivered, and the log backend answers that
without being able to be the cause.

## Port collisions with our own compose

Upstream publishes **Postgres on 5432 and Redis on 6379** — the same ports as the
`local-db` profile in our [`docker-compose.yml`](../../docker-compose.yml). The two
stacks cannot run at once unmodified. Options, in order of preference:

1. Don't use `--profile local-db`. The default setup already talks to managed
   Postgres/Redis, so there is nothing to collide.
2. Remap our `local-db` ports to `5433` / `6380` and update `DATABASE_URL` / `REDIS_URL`.
3. Remove the host port publications from upstream's Postgres and Redis — Langfuse
   reaches them over the compose network and nothing else needs them.

Langfuse's own Redis is a BullMQ job queue for Langfuse. **Do not point our `REDIS_URL`
at it**: our Redis holds the HITL proposals, the JWT blocklist and the rate-limit
windows, and sharing a keyspace with a queue that flushes itself is a way to lose them.

## What this costs

ClickHouse is not a light process. Expect a couple of GB of RAM for the full stack idle,
which is why it is a separate opt-in file rather than something `docker compose up`
starts by default. For a laptop demo, Langfuse Cloud is the cheaper path; self-hosting
is the right answer once real claim data is involved, since traces carry claimant
identities even after masking.

## Resources

- Self-hosting guide and configuration reference: <https://langfuse.com/docs/self-hosting>
- Upstream compose: <https://github.com/langfuse/langfuse/blob/main/docker-compose.yml>
