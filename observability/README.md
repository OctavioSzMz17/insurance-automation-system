# Observability

Structured logging and tracing for the whole monorepo. Rule 9's contract: **no service
ever imports a tracing SDK.** Agents, routes and MCPs depend on the `Tracer` protocol in
[`tracers/base_tracer.py`](tracers/base_tracer.py); a backend is a new file under
`tracers/` plus a row in the factory. Swapping or adding one must not touch a call site.

```bash
OBSERVABILITY_BACKEND=log    python -m uvicorn app:app --app-dir ai-orchestrator
OBSERVABILITY_BACKEND=noop   # the default: costs nothing, nothing breaks
OBSERVABILITY_BACKEND=log,recording   # comma-separated composes them
```

## Using it

```python
from observability import SpanKind, get_tracer

async with get_tracer().span("analyst.think_act", kind=SpanKind.GENERATION, step=3) as span:
    turn = await llm.complete(...)
    span.set_usage(input_tokens=turn.input_tokens, output_tokens=turn.output_tokens,
                   model=llm.model)
    span.set_attributes(stop_reason=turn.stop_reason)
```

`get_tracer()` returns a no-op until `configure_tracing()` runs in the app lifespan, so
importing instrumented modules never needs setup — tests and scripts just work. Spans
nest automatically via a `ContextVar`, which is per-task, so two claims analysed
concurrently never adopt each other's spans.

## Span catalog

One session per claim (`session_id = pipeline_id`), so `/analyze`, `/audit` and
`/confirm` — three separate HTTP requests — read as one timeline.

| Span | Kind | Key attributes |
|---|---|---|
| `claim.upload` | root | `bytes`, `sha256_12`, `content_type` |
| `claim.analyze` | root | `pipeline_id`, `claimant_sub`, `operator_sub`, `outcome`, `review_flags` |
| `analyst.run` | internal | `steps_used`, `severity`, `policy_id`, `max_steps`, `model` |
| `analyst.think_act` | **generation** | `step`, `stop_reason`, `emitted`, `prompt_version`, + usage |
| `tool.<domain>.<tool>` | tool | `identity_forced`, `caller_sub`, `caller_role`, `write_refused` |
| `analyst.validate` | internal | `severity_claimed`, `severity_computed`, `severity_overridden`, `retries` |
| `analyst.escalate` | event | `steps`, status message = the escalation reason |
| `verification` | internal | `flags`; score `review_flags` |
| `check.coverage` / `.ownership` / `.identity` / `.amount` | event | the verdict fields; message = the reason |
| `auditor.run` | internal | `verification_flags`, `decision`, `evidence_items` |
| `auditor.think_act` | **generation** | `step`, `stop_reason`, `prompt_version`, + usage |
| `claim.audit` | root | `proposal_id`, `outcome` |
| `claim.confirm` | root | `approval`, `approver_sub`, `outcome`; scores `human_decision`, `persisted` |
| `mutation.gate` | internal | `flags`, `blocked`, `decision`; score `gate_blocked` |

Two of these exist specifically to make an invariant legible rather than merely true:

* `analyst.validate` records `severity_claimed` next to `severity_computed`. When a
  document talks the model into `CRITICAL` on an $800 claim, the trace shows the claim
  and the override side by side.
* `tool.*` records `identity_forced` with the caller the server imposed. A model that
  spoofs `caller_sub` leaves no trace of having succeeded, because it did not.

## Masking

Two separate concerns, deliberately not conflated — see [`masking.py`](masking.py).

**Volume.** Document text never travels in a span, at any masking level. Instrumentation
sends `summarize_text()` — a character count and a short hash. A claim PDF is tens of
thousands of characters: useless in a UI, expensive in a trace store, fatal over SSE.

**Privacy.** Emails and names are pseudonymised at `pii` level and above. The pseudonym
is stable, so "the same claimant across these traces" stays answerable without the trace
carrying an identity. `OBSERVABILITY_MASK` is `none` in development (traces are only
useful if you can read them) and `pii` everywhere else — production never has to remember
to switch it on.

`evals/test_tracing.py::test_no_document_text_reaches_a_span` enforces the first rule
against a real claim.

## Tracing fails open

The verification gate in `MutationHandler` fails **closed**: if it cannot establish that
a policy is in force, it refuses the approval, because letting a fraudulent claim through
is worse than refusing a valid one.

Tracing is the mirror image. A backend that throws is caught, logged **once**, and the
traced code continues. A tracer that fails closed takes down the product it exists to
observe. `test_a_broken_backend_does_not_break_traced_code` pins this.

That guard is not theoretical: it caught a real bug during development. `LogTracer`
splatted the span payload into `logging`'s `extra=`, where `name` and `message` collide
with reserved `LogRecord` attributes. Logging raised, the guard swallowed it, and the
backend silently emitted nothing — exactly the degradation it is designed for, and the
reason `test_log_tracer_actually_emits` now exists.

## Adding a backend

1. New file in `tracers/`, subclassing `BaseTracer`, overriding `on_start` / `on_end`.
2. One row in `_BACKENDS` in [`tracers/__init__.py`](tracers/__init__.py).
3. Nothing else. No call site changes.

Planned:

* **`eventbus`** — Redis `RPUSH` + `PUBLISH` → SSE → the live graph UI. The live view is
  another *consumer* of the same spans, not another instrumentation, which is what
  `CompositeTracer` is for.

## The Langfuse backend

```bash
OBSERVABILITY_BACKEND=log,langfuse
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=http://localhost:3000   # omit for Langfuse Cloud
```

Self-hosting: [`infra/observability/README.md`](../infra/observability/README.md).

**Mapping.** Langfuse 4 has native `tool` and `event` observation types, so `SpanKind`
maps one-to-one and nothing is flattened into a generic span. Span attributes become
`langfuse.observation.metadata.<field>` — one key each, so they are filterable in the UI
rather than an opaque blob. `set_usage` becomes `usage_details` + `model`, which is what
Langfuse computes cost from. A failed span becomes `level="ERROR"` with the status
message.

**Scores go to the trace, not the span.** `review_flags`, `gate_blocked` and
`human_decision` are facts about the *claim*, not about the span that noticed them, and a
trace-level score is what a dashboard can chart over time. Scores also travel over the
HTTP API rather than the span exporter, so a score failure is isolated — losing a metric
must not cost us the trace.

**Bridging two context models.** The SDK is context-manager-first
(`start_as_current_observation` nests by entering a `with` block), while `BaseTracer` owns
the context management and exposes hooks. The bridge is `start_observation`, the SDK's
manual constructor, which creates a child of an explicit parent without touching the
ambient OTel context — so this backend keeps its own `span_id → observation` map and
never fights the SDK over who owns the current context.

**Verified offline.** The client accepts a `span_exporter`, so
`tests/unit/test_langfuse_tracer.py` swaps in an in-memory OTel exporter and asserts the
spans the SDK *would have shipped*: parentage, observation types, session propagation,
usage, masking, error levels. Integration code written from documentation and never
executed is a guess; these tests are what make this file a fact. They also pin the SDK
contract — a future release that renames `start_observation` fails the tests instead of
silently shipping nothing.

Two things found while writing them, both worth knowing:

* `Langfuse` is a **singleton per `public_key`**. Constructing a second instance with the
  same key returns the first and silently ignores the new arguments, including
  `span_exporter`.
* Credentials missing means the backend **refuses to build**, and `build_tracer` then
  degrades to a no-op with a warning. A service must not run believing it is traced when
  it is not — and in production `config.cross_validate()` refuses to boot at all.

## Prompts stay in Python

`SYSTEM_PROMPT` lives in `ai-orchestrator/core/prompts/` and is the source of truth. A
trace store records *which* version ran; it never serves the prompt. Fetching a prompt
over the network would put an observability dependency in the critical path of a
regulated pipeline, and editing prompts without a deploy is not worth a pipeline that
stops when the trace store does.

The version is content-addressed — `sha256(SYSTEM_PROMPT)[:12]`, computed at import. No
number to bump, and the recorded version cannot disagree with the text that ran.

## Logging

[`logging.py`](logging.py) is the canonical structured JSON logger (Rule 9: no `print()`
anywhere). `mcp-servers/_common/logging.py` re-exports it, so the MCP servers are
unchanged. Everything passed via `extra=` becomes a top-level JSON key, which is what
makes the log queryable rather than merely readable.
