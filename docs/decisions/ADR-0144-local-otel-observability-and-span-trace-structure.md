# ADR-0144: Local OpenTelemetry Observability and Span-Trace Structure

Date: 2026-06-17
Status: Proposed

## Context

ADR-0045 established the vendor-neutral tracing contract (`none | console |
jsonl | opik`, JSONL canonical, Opik explicit opt-in). In practice the only
*rich* backend is Opik, and self-hosted Opik is too heavy for local development:
it is an 8-container stack (MySQL, Redis, ZooKeeper, ClickHouse, MinIO +
init, Java backend, Python backend, frontend). Teams want Opik in the **cloud**
for evaluation, but local runs need a lightweight alternative.

LMCache's reference observability stack (docs + `examples/observability`) was
reviewed as a pattern source. Its **metrics** do not apply to us — we run no
local vLLM+LMCache serving layer; we call external APIs (MARA/OpenAI) through
`OpenAICompatibleBackend`. But its **topology** does: OTLP Collector →
Prometheus (metrics) + Tempo (traces) → Grafana, four small containers, local
disk. That is the right-sized local alternative to self-hosted Opik.

Three review rounds (infra / LLM / ontology, then GraphRAG / Graph-DBA / prompt)
surfaced that the harder problem is not the backend but **what spans we emit**:

- `local_engine.ask()` runs ~12 internal stages (schema → plan → execute →
  multi_plan → neighbor_fallback → repair → vector → chunk_fallback →
  ontology_context_check → deterministic_answer → generation) but emits **one
  flat `sdk.query` span**; stage timings are flattened into a
  `latency_breakdown_ms` metadata dict, not a nested span tree. `SessionTrace`
  already supports parent/child nesting but `ask()` does not use it.
- Cypher execution (`store/graph.py:query`, `query/executor.py`) is a bare
  `session.run()` — **no span, no DB-side timing, no row count**; failures only
  hit the Python logger. `workspace_id` is a parameter, never a span attribute,
  so the multi-tenant contract is invisible to observability.
- ADR-0111 adopted `neo4j-rust-ext` (a Rust PackStream codec drop-in, selected
  at install time, logged once per process as `rust-ext active` /
  `pure-python active`). Its measurement showed **71–91% of client-side query
  cost is result deserialization/hydration** (W2: 3.57× speedup). But that
  hydration time is buried inside `[record.data() for record in result]` and
  the active codec is invisible to traces — so the rust-ext win cannot be
  observed or regression-monitored (e.g. a deploy that loses the wheel silently
  falls back to pure-python and ~3.5× slower hydration).
- **Retrieved context is dark**: the `records` (and chunks/observations) fed to
  synthesis are never captured — only the final answer is. For Graph-RAG, the
  retrieved evidence is the first thing you need to debug "why this answer".
- Because we call **external** LLM APIs, the prompt is the main lever we control,
  yet prompt observability is the weakest: native spans capture only
  `text_preview[:200]`, with no system/user split, no temperature/model params,
  and no template identity. Full prompt capture exists **only** via Opik's
  `track_openai`, so dropping Opik locally drops prompt visibility entirely.
  Two already-computed signals are never emitted: the `prompt_version` hash
  (`index/metadata.py`, stamped on `MENTIONS` edges) and
  `CompiledOntologyContext.kv_cache_layout().stable_prefix_hash`
  (`ontology_context.py`) — the latter is exactly the prefix-cache reuse signal
  that governs external-API cost.
- Ontology governance is under-traced: enforcement mode (strict/guided/open),
  per-error validation detail, reified-Observation counts, and guardrail-selector
  (ADR-0123) decisions are logged to stdout or returned as objects, not emitted
  as structured spans/metrics.

## Decision

Extend ADR-0045 with a local OpenTelemetry path and a structured span tree,
keeping Opik as the unchanged cloud team backend.

### 1. New `otlp` tracing backend (extends ADR-0045)

- Add `otlp` to the supported `SEOCHO_TRACE_BACKEND` values:
  `none | console | jsonl | opik | otlp`.
- `OTLPBackend` implements the existing `TracingBackend` ABC and exports spans
  over OTLP gRPC to a Collector. Because `tracing.py` already accepts a backend
  **list**, the same spans can fan out to both Opik (cloud) and OTLP (local)
  with no duplicate instrumentation. Intended split: `opik` in cloud, `otlp`
  locally; JSONL remains the canonical neutral artifact.
- Config additions: `SEOCHO_TRACE_OTLP_ENDPOINT` (default
  `http://localhost:4317`), `OTEL_SERVICE_NAME` (default `seocho`).

### 2. Content-capture policy (the root fix for "Opik felt heavy")

- **Attributes are always emitted** (cheap, joinable): hashes, versions, token
  counts, model/params, row counts, `workspace_id`.
- **Content is gated** (expensive): full prompt/completion text, full Cypher,
  retrieved-record bodies emit only when `SEOCHO_TRACE_CAPTURE_CONTENT=1`, with
  truncation and head-sampling. Default off → light by default, deep on demand.
- Metric labels never carry raw Cypher/prompt text — only a `*_template_hash`
  (reuse the existing `_short_hash` pattern) to avoid cardinality blow-up.

### 3. Nested span tree for `ask()` (no new framework)

Promote the existing `StageTimer` context managers to span emission and use the
existing `SessionTrace` as the root, yielding:

```
rag.ask                 (root = SessionTrace; attrs: workspace_id, request_id)
├─ rag.decompose        slots, resolved/missing
├─ rag.arbitrate        route, rationale         (fold in today's semantic.route)
├─ rag.compile_cypher   intent, query_template_hash
├─ rag.execute          db.name, db.rows_returned, db.duration_ms, workspace_id
├─ rag.retrieve_ctx     n_nodes, n_chunks, scores            ★ new, top priority
└─ rag.synthesize       gen_ai.* + prompt_version + stable_prefix_hash
```

`sdk.query` / `sdk.extraction` remain as aggregate spans for backward
compatibility; the new spans nest under the `rag.ask` root.

### 4. First-class `workspace_id` + DB instrumentation

`rag.execute` wraps `graph_store.query()` with OTel `db.*` semantic-convention
attributes (`db.system=neo4j`, `db.name`, `db.statement` content-gated,
`db.rows_returned`) and carries `workspace_id` as a first-class attribute on
every span. `X-Request-ID` (already in middleware) is threaded into a Cypher
comment for DB-log ↔ trace correlation. Slow-query `PROFILE` capture is sampled,
not always-on.

**Rust-ext-aware timing split (ADR-0111).** Because hydration dominates
client-side cost, `rag.execute` splits the single timing into three attributes
instead of one `db.duration_ms`:

- `db.duration_server_ms` — from the cheap `ResultSummary`
  (`result_available_after` / `result_consumed_after`), no `PROFILE` needed
- `db.duration_hydrate_ms` — time spent in the `record.data()` loop (the codec
  cost rust-ext targets)
- `db.rows_returned` — denominator for per-row hydration cost

The active PackStream codec (`rust-ext` / `pure-python`, already detected at
init) is emitted as an OTel **resource attribute** `db.client.codec` (process-
global, not per query), so every trace records which codec produced its
latency and a silent fallback to pure-python is immediately visible.
`execute_write()` additionally surfaces `result.consume().counters`
(`db.nodes_created`, `db.relationships_created`, `db.properties_set`) as span
attributes — already computed today, currently unused for observability.

### 5. GenAI prompt observability via OTel conventions

`rag.synthesize` (and the extraction LLM call) adopt `gen_ai.*` semantic
conventions: `gen_ai.request.model`, `gen_ai.request.temperature`,
`gen_ai.usage.input_tokens/output_tokens`, plus SEOCHO attributes
`prompt_version`, `prompt_template`, `ontology_context_hash`,
`stable_prefix_hash`. `gen_ai.prompt`/`gen_ai.completion` are content-gated.
This restores on the local OTLP path what Opik's `track_openai` gives for free
on cloud, and makes "prompt variant ↔ cache reuse ↔ answer quality" joinable.

### 6. Ontology governance spans/metrics

Emit `enforcement_mode` and per-error validation detail on the extraction span;
add `seocho_validation_errors_total{mode,ontology}`,
`seocho_observations_reified_total`, `seocho_arbiter_route_total{route}`; and
give the guardrail selector (ADR-0123) an audit span when invoked.

### 7. Local compose profile (pattern, not LMCache manifest copy)

Add `docker-compose.observability.yml` under a `--profile observability`:
OTel Collector + Tempo + Prometheus + Grafana, reusing the existing
`seocho-net`, pinned image tags, traces→Tempo / metrics→Prometheus →
Grafana. Dev-grade defaults are acceptable (local only); production hardening
(auth, durable storage) is out of scope and noted as such.

## Validation

This ADR is **Proposed**; no measurement yet. Acceptance for the implementation
slices (tracked as `seocho-*` beads under this ADR):

- with `SEOCHO_TRACE_BACKEND=otlp`, a single `ask()` produces a nested
  `rag.ask` trace in Tempo with the six child spans, queryable by TraceQL
  (`{ name="rag.execute" && span.workspace_id="..." }`).
- `workspace_id` present on every span; `db.rows_returned` and `db.duration_ms`
  populated on `rag.execute`.
- `prompt_version` and `stable_prefix_hash` present on `rag.synthesize`;
  full prompt text present **only** when `SEOCHO_TRACE_CAPTURE_CONTENT=1`.
- `opik` (cloud) behavior unchanged when `otlp` is not in the backend list.
- the four-container profile boots and Grafana shows the trace + the
  `seocho_*` metrics. Per the team's ontology-experiment rule, before/after
  observability deltas are recorded under `docs/decisions/`.

## Consequences

- Local development gets rich observability at ~4 containers instead of Opik's
  ~8, with Opik untouched as the cloud team backend — matching the stated
  "cloud=Opik / local=separate" split.
- Multi-stage Graph-RAG failures become debuggable: each stage is its own span,
  and retrieved context (the previously dark `rag.retrieve_ctx`) is captured.
- Prompt/cache observability — the real control surface in an external-API
  deployment — is restored on the neutral/local path, not just in Opik.
- The content-capture policy makes heaviness opt-in, addressing the original
  "Opik is too heavy" complaint at its root (always-on full-text capture).
- Tradeoffs: adopting `gen_ai.*`/`db.*` conventions and a span tree adds
  surface area to `tracing.py` and the `ask()` hot path; span emission must stay
  exception-isolated (as today) so observability never breaks answering. The
  LMCache metrics families remain inapplicable until/unless a local vLLM+LMCache
  serving layer is introduced — at which point it scrapes the same Prometheus.
- Making the server/hydration split observable turns the ADR-0111 rust-ext
  investment into a monitored, regression-guarded property rather than a
  one-off benchmark — and the same span attributes apply unchanged under either
  codec.
- Related: ADR-0045 (tracing contract, extended here), ADR-0031 (evidence
  bundle — the `rag.retrieve_ctx` payload source), ADR-0103 (arbiter, whose
  `semantic.route` span folds into `rag.arbitrate`), ADR-0111 (neo4j-rust-ext,
  whose hydration win the `rag.execute` timing split makes observable),
  ADR-0123 (guardrail selector audit span). Ticket `seocho-ub5` (provider/model-
  aware meta-prompt) is adjacent but distinct (output robustness, not
  observability).
