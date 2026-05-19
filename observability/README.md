# Observability — Lía's Tracing & Telemetry

Everything we use to **see what Lía is doing**: live traces of every
LLM call, every tool invocation, every retry, with token counts and
costs attached. Engineer-facing — adjusters never see this stack.

> **Status:** D1.1 complete (Langfuse infrastructure up). Agent
> instrumentation (D1.2 / D2.x) wires the orchestrator to actually
> ship traces here. Until then, the dashboard will be empty.

## What lives here

```
observability/
├── docker-compose.yml      ← Langfuse self-hosted stack (this doc)
├── README.md               ← you are here
├── __init__.py             ← (D2.1) Python entry point
├── logging.py              ← (D3.1) JSON structured logging
├── metrics.py              ← (D4.1) Prometheus /metrics emitter
├── evaluation.py           ← (D6.1) eval harness vs. golden set
└── tracers/
    ├── base_tracer.py      ← (D2.1) abstract tracer interface
    └── langfuse_tracer.py  ← (D2.1) Langfuse adapter
```

## The Langfuse stack

Six containers, all on the existing `insureai-dev` Docker network:

| Service             | Purpose                                      | Host port |
|---------------------|----------------------------------------------|-----------|
| `langfuse-web`      | Dashboard UI + ingest API                    | **3001**  |
| `langfuse-worker`   | Async event processor (no UI)                | —         |
| `langfuse-pg`       | Postgres for org/project/user metadata       | —         |
| `langfuse-ch`       | ClickHouse for trace events (time-series)    | —         |
| `langfuse-redis`    | Queue between web and worker                 | —         |
| `langfuse-minio`    | S3-compatible blob store for large payloads  | —         |

Combined idle footprint: ~1.5 GB images, ~600 MB RAM. Heavy but
standard. In production you'd point ClickHouse and Postgres at
managed services; the rest stay in-cluster.

## Quick start

```bash
# Always start the app stack first — it owns the `insureai` network
docker compose -f infra/docker/docker-compose.yml up -d

# Bring up observability (subscribes to the same network)
docker compose -f observability/docker-compose.yml up -d

# Wait ~30s for ClickHouse migrations on the very first run
docker compose -f observability/docker-compose.yml logs -f langfuse-web
# Look for: "Server listening on http://localhost:3000"
```

Then open **http://localhost:3001** in a browser.

### Default credentials

The `LANGFUSE_INIT_*` env vars in `.env` auto-create the org, project,
user, and API keys on the very first boot, so there's nothing to click
through.

| Field                | Value (from `.env`)            |
|----------------------|--------------------------------|
| Admin email          | `admin@insureai.local`         |
| Admin password       | `insureai-admin-dev`           |
| Org                  | `InsureAI`                     |
| Project              | `Orchestrator (dev)`           |
| Public key (`pk-…`)  | `pk-lf-dev-insureai`           |
| Secret key (`sk-…`)  | `sk-lf-dev-insureai`           |

Change every one of these before deploying anywhere shared.

## Shutting down

```bash
# Stop containers (data is preserved)
docker compose -f observability/docker-compose.yml down

# DESTROY all data — irreversible, only useful for clean reruns
docker compose -f observability/docker-compose.yml down -v
```

## Common gotchas

**`network "insureai-dev" not found`**
The app stack hasn't been started yet. Run the `infra/docker` compose
up first; the observability stack only subscribes to the network, it
doesn't create it.

**`langfuse-web` keeps restarting on first boot**
Normal for ~30s while ClickHouse migrations run. Watch the worker
logs; once it prints `migrations applied successfully`, the web
container will stabilize on its next restart.

**Dashboard loads but is empty after submitting claims**
Expected for now. D1.1 only stands up the infrastructure. D1.2 wires
the orchestrator to actually emit traces. Once that ticket lands,
every claim submission shows up here as a trace tree.

## Who sees the "Open in Langfuse" link (D5.3)

The embedded trace viewer in the adjuster UI (D5.2) is for everyone —
analyst, auditor, admin — because the curated tree (steps, latencies,
costs, tokens) is safe operational information.

The **deep-link button to the Langfuse dashboard** is restricted to
**admin** role only. The dashboard exposes engineer-level detail —
raw prompts, every tool input/output, retry trees — which could be
confusing to non-technical adjusters and risks leaking PII embedded
in prompts. Gating is enforced at the backend: `GET /claims/{id}/trace`
returns `langfuse_url=null` for non-admin callers, so even DevTools
inspection of the JSON won't expose the URL.

### To test as an admin in dev

1. Edit `ui/.env.local` and add `NEXT_PUBLIC_DEV_ROLE=admin`
2. Restart UI: `docker compose -f infra/docker/docker-compose.yml up -d --force-recreate ui`
3. Refresh the browser. The "Abrir en Langfuse" link appears at the top
   of the expanded reasoning tree.

### Production note

When this app hits production, replace the `/auth/token` dev endpoint
with a real IdP. The same role mapping (`admin` → engineer access)
should be respected when issuing JWTs upstream.

## Log format toggle (D3.1)

The orchestrator emits either human-readable or JSON logs based on
`LOG_FORMAT` in `.env`. The trace_id from D2.4 is included in both.

```bash
# Dev default — pipe-separated, easy to read
LOG_FORMAT=text
2026-05-19T04:21:14 | INFO | trace=abc-… | routes.claims | POST /claims/…

# Production / log aggregators (Datadog, Loki, ELK, CloudWatch…)
LOG_FORMAT=json
{"trace_id":"abc-…","timestamp":"2026-05-19T04:21:14","level":"INFO",
 "logger":"routes.claims","message":"POST /claims/…"}
```

Flip the value, restart only the orchestrator
(`docker compose -f infra/docker/docker-compose.yml up -d --force-recreate orchestrator`),
no rebuild needed.

Uvicorn's own access logs (`INFO: 127.0.0.1 - GET /health 200`) keep
their default format — they live on Uvicorn's logger, not ours. A
follow-up ticket can unify them if needed.

## Eval harness (D6.1)

A curated set of claims with known-correct outcomes that we replay
through Lía whenever we change prompts, agent logic, or models.
Catches regressions before they ship.

### Layout

```
observability/evals/
├── _generate_fixtures.py     ← one-off PDF generator (reportlab)
└── golden/
    └── <case_id>/
        ├── claim.pdf          ← input the agent sees
        └── expected.json      ← decision + severity + flag asserts
```

`expected.json` shape:

```json
{
  "name":  "Clean approval — Carlos Rodríguez rear-end",
  "notes": "Why this case exists",
  "expected": {
    "decision":      "APPROVED",
    "severity":      "LOW",
    "must_flag":     [],
    "must_not_flag": ["outside", "expired"]
  }
}
```

`decision` / `severity` accept `null` to skip that assertion. The
flag substrings are matched case-insensitively against the auditor's
rationale + analyst's flags + summary.

### Run

```bash
# List available cases
docker exec insureai-orchestrator python -m observability.evaluation list

# Run all
docker exec insureai-orchestrator python -m observability.evaluation run

# Run one
docker exec insureai-orchestrator python -m observability.evaluation run \
    --case case_001_clean_approval

# Machine-readable JSON (for CI later)
docker exec insureai-orchestrator python -m observability.evaluation run --json
```

Exit code is non-zero when any case fails — drop the `run` command
into a GitHub Action and you've got prompt-regression CI for free.

### Add a new case

1. Copy an existing folder under `golden/` and rename it.
2. Replace `claim.pdf` (write your own PDF or extend
   `_generate_fixtures.py` with another CASE entry).
3. Update `expected.json` with what Lía should produce.
4. Run the harness; iterate until it passes against the current prompts.

### Where the traces go

Each eval emits a Langfuse trace named `eval-{case_id}` so the
dashboard keeps them separate from real-claim traffic. Filter by
`name:eval-*` in Langfuse to see only eval runs.

### What the harness does NOT do

- It does not write to the DB (`persist=False`). Eval runs never
  create claim rows.
- It does not auto-score subjective fields ("reasoning quality").
  Only matches expected exact strings — assertion semantics, not LLM
  grading semantics.
- It does not loop / retry. Each case runs once; flakiness shows up
  as flakiness.

## Privacy

- `TELEMETRY_ENABLED=false` — Langfuse's own anonymous usage pings
  are off. Nothing leaves the box.
- All data (Postgres, ClickHouse, MinIO) is stored in named Docker
  volumes on disk. Wipe with `down -v`.
- The S3-compatible store is local MinIO — no actual AWS account
  involved.

## Production notes (when we get there)

1. Generate real secrets: `openssl rand -hex 32` for
   `LANGFUSE_ENCRYPTION_KEY`, `openssl rand -base64 24` for the rest.
2. Drop `LANGFUSE_INIT_*` — users should be invited through the UI.
3. Point `LANGFUSE_HOST` at the production URL behind TLS.
4. Restrict access via reverse-proxy auth or VPN — Langfuse's own
   auth is fine for engineers, not for public exposure.
5. Externalize Postgres + ClickHouse to managed services or
   StatefulSets with backups configured.
