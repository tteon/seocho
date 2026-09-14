# Inference cost, serving and schema workbench

This operator prototype connects request receipts, engine traces and explicit
cost entries in one private HTML dashboard. The unit of accounting is an LLM
request, including failed attempts. A successful HTTP request is not proof of
a correct or supported financial answer. Keep question-level quality evaluation
and cost per quality-approved answer alongside this report.

## Run a report

Install the locked local dependencies with `uv sync --locked --extra ci`.
Run from the repository root:

```bash
uv run python scripts/benchmarks/inference_workbench.py report \
  --requests .seocho/my-run/requests.jsonl \
  --spans .seocho/my-run/engine-spans.jsonl \
  --cost-ledger .seocho/my-run/costs.jsonl \
  --out .seocho/my-report
```

`--spans`, `--cost-ledger`, `--baseline` and `--telemetry` are optional. Open
`index.html` directly in a browser. Filters cover model, tenant, route and
configuration; selecting a request shows correlation IDs, provenance and
missing fields. `summary.json` and `alerts.json` are portable artifacts.
Output paths must be fresh. Keep reports private: they contain operational
identifiers even though the probe stores output hashes rather than source text.

Each request requires `request_id`, `model`, `tenant`, `route`, `workspace_id`,
`configuration_id`, `workload_id` and a `status`. Optional measurements are
`input_tokens`, `output_tokens`, `duration_ms`, `ttft_ms`, `itl_ms`, `queue_ms`,
`prefill_ms`, `decode_ms`, `cost_usd` and `cost_basis`. Durations use milliseconds.
`itl_ms` requires `itl_source=engine_trace|token_timestamps`. Unknowns are null.
Use configuration and workload IDs that cover model revisions, sampling,
hardware, serving flags, prompt set, concurrency and cache-state policy.

A cost-ledger line is:

```json
{"request_id":"request-1","cost_usd":0.002,"cost_basis":"provider invoice, input + output charge"}
```

Duplicate, unknown or already-priced requests are rejected. Groups with missing
costs show priced coverage and known subtotal, but no misleading total or
$/1M. Both output-token and input-plus-output-token denominators are shown.
Failures and retries remain in the numerator. Cached API tokens must use the
provider's actual pricing when constructing the ledger.

For self-hosting, `allocate_run_cost` in `seocho.eval.inference_spine` offers an
explicit **estimated** allocation by reported input plus output tokens. Supply
total resource USD, unallocated USD and the source of the amount; retain its
allocation receipt beside the dashboard. This conserves attributed plus
unallocated cost, requires complete usage, and does not claim to measure GPU
consumption per tenant. Include target, draft, CPU/SSD, startup, failed runs and
idle charges in the run-level total. An hourly estimate is not an invoice.

## Trace and metrics boundaries

The existing SEOCHO JSONL/OTLP backend remains the production trace spine.
The probe emits W3C `traceparent` and `X-Request-Id`, preserving a corresponding
portable `client-spans.jsonl`. The engine span importer accepts the existing
normalized OTLP file-receiver rows with `service_name`, `name`, `trace_id`,
`span_id`, `status`, and `attributes`.

For vLLM `llm_request` spans, it joins exact `gen_ai.request.id` values to
response IDs. A unique trace match is a fallback only for a single request;
ambiguous joins stay unassigned. It converts `gen_ai.latency.time_in_queue`,
`time_in_model_prefill`, `time_in_model_decode` and `time_to_first_token` from
seconds to milliseconds. Engine TTFT stays separate from client TTFT. If
vLLM detailed traces are disabled or lost, per-request phase timings are unknown.

Prometheus before/after snapshots supply engine-window means, prefix hits and
speculative acceptance. Windows reject counter resets or changed series sets.
They are not per-tenant measurements. Streaming chunk gaps are stored separately
and are never labelled token ITL: one chunk can contain several accepted tokens.
DCGM GPU utilization, framebuffer memory and power remain device-scoped. A
missing DCGM exporter is a visible gap; nvidia-smi observations are not DCGM data.

Alerts include request errors, incomplete/empty case answers, matched-cohort
cost spikes and TTFT p95 increases. The default comparison requires 20 measured
requests and a 1.5x increase. Output-hash variation under identical inputs is
reported as non-determinism, not automatic quality regression. Alerts are local
JSON/UI records; dispatch to paging/chat systems is operator-owned.

## Draft-target serving prototype

On a provisioned GPU host, prepare a pinned target revision and an immutable
local draft snapshot compatible with that installed vLLM version. This utility
does not select a model pair or rent hardware implicitly.

```bash
uv run python scripts/benchmarks/inference_workbench.py serving-plan \
  --target "$TARGET_MODEL" --revision "$TARGET_REVISION" \
  --draft-snapshot "$DRAFT_SNAPSHOT" --out .seocho/serving-plan.json

uv run python scripts/benchmarks/inference_workbench.py serve-arm \
  --model "$TARGET_MODEL" --revision "$TARGET_REVISION" \
  --draft-snapshot "$DRAFT_SNAPSHOT" --arm spec1-prefix1-chunked1 \
  --tenant research --workspace-id finance-eval --route graph-agent \
  --configuration-id pinned-serving-configuration \
  --prompts .seocho/prompts.jsonl --max-calls 20 --max-tokens 256 \
  --startup-seconds 600 --max-seconds 900 \
  --dcgm-url http://127.0.0.1:9400/metrics --out .seocho/serving-arm
```

Prompt lines contain `messages` in chat format. Eight arms independently toggle
draft-model speculative decoding, automatic prefix caching and chunked prefill.
`serve-arm` starts an owned process group on an unused loopback port, checks
`/v1/models`, runs the bounded streaming probe and stops that group in `finally`
with a hard timer. It does not stop other processes. Failed startup is evidence
of a compatibility gap; do not treat it as a zero-latency result.

Use `probe --base-url ... --metrics-url ...` with the same request flags to
measure an already running endpoint without managing its lifecycle. Credentials
come from the environment named by `--api-key-env` (default `INFERENCE_API_KEY`),
never from persisted argv. No request-specific cost is invented by the probe.

Acceptance is **accepted draft tokens / proposed draft tokens**. Accepted tokens
per draft step are separate; target bonus tokens are not draft acceptance.
Actual improvement requires repetitions, identical prompts/sampling/hardware,
explicit cold/warm policy, matched concurrency and an exclusive engine window.
The sequential probe alone is not a capacity benchmark. Preserve answer-quality
results as the gate before choosing a cheaper serving configuration.

vLLM syntax and support change across releases; pin the image and verify against
the [official speculative decoding documentation](https://docs.vllm.ai/en/latest/features/speculative_decoding/).
Prefix caching addresses prefill reuse, not a direct decode speedup.

## DozerDB schema observations

```bash
uv run python scripts/benchmarks/inference_workbench.py schema \
  --database neo4j --database-scope --sample 500 --out .seocho/db-schema.json

uv run python scripts/benchmarks/inference_workbench.py schema \
  --database neo4j --workspace-id finance-eval --sample 500 \
  --out .seocho/workspace-shape.json
```

Connection values come from `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` or the
environment-variable names supplied by the corresponding CLI flags. The session
uses read access and fixed queries with a 30-second transaction timeout.

Database scope discovers executable procedures first, prefers available READ
`apoc.meta.nodeTypeProperties`/`relTypeProperties`, and falls back to
`db.schema.*`. It also reads `SHOW INDEXES` and `SHOW CONSTRAINTS` for operators.
It inventories `apoc.meta.schema` without issuing a redundant full scan.
Availability, sampling and errors are retained. No admin writes are exposed.

Workspace scope uses parameterized Cypher and checks both endpoints and the
relationship's `_workspace_id`. It samples physical labels, `n.kind` and
`r.relation` for generic projections; other projection conventions need an
explicit adapter. Empty samples are not proof that a type is forbidden.
Database-wide APOC results must not be handed to a shared-workspace agent as
if they were workspace-filtered. Neither observed mandatory properties nor
database constraints automatically become FIBO axioms.

Existing procedures plus fixed Cypher currently cover the proposed interface.
Add a custom Java READ procedure only after recording a reproducible remaining
gap, matching the actual DozerDB/Neo4j plugin ABI, and validating on that server.
See [APOC metadata](https://neo4j.com/docs/apoc/current/overview/apoc.meta/) and
[custom procedures](https://neo4j.com/docs/java-reference/current/extending-neo4j/procedures/).

### Procedure heap accounting and page-cache experiments

`ProcedureMemory` is a preview procedure API. Where the deployed server supports
injection, use a transaction-bound tracker for large traversal frontiers,
visited-ID collections and materialized evidence buffers. Account growing
allocations, avoid double-counting shared objects, and release trackers on
normal stream closure and exceptional exits. Closing a tracker does not force
Java GC, and heap accounting does not account automatically for arbitrary native
allocations or protect against every process OOM.

Validate the exact runtime JAR/ABI and injected tracker before enabling a custom
plugin. A useful live acceptance test fixes the graph, result set and transaction
memory limit, compares untracked/tracked bounded traversals, and verifies that
oversized requests fail within the transaction budget while a following small
query still succeeds. Record PROFILE allocation, heap/GC observations,
termination behavior, latency, visited nodes/edges and result completeness.
Also exercise early result closure, cancellation and exception paths. Do not
force an actual database OOM just to obtain a comparison.

Page-cache warmup is a separate I/O intervention. First inspect the server's
built-in warmup settings and edition support. `apoc.warmup.run` is version and
store-format dependent and deprecated; do not make it a mandatory agent tool.
An operator can instead replay a fixed, training/development-derived set of
read-only lookups before the measured run. This touches actual required
properties and paths, rather than assuming `count(*)` visits store pages.
It does not guarantee page pinning or warming every index/property store.

Compare no explicit warmup, representative-query warmup, and supported native
warmup with fixed page-cache capacity and dataset. Keep warmup time and I/O in
the cost boundary; measure first-query/tail latency and page faults alongside
warm-state throughput. A process restart does not necessarily empty the OS page
cache. Never select warmed entities from held-out answers or silently warm only
one ontology arm. A targeted custom warmup procedure is justified only if this
replay/native comparison exposes a specific capability or overhead gap.

References: [procedure memory tracking](https://neo4j.com/docs/java-reference/current/extending-neo4j/procedures/),
[page-cache warmup](https://neo4j.com/docs/operations-manual/current/performance/disks-ram-and-other-tips/),
[APOC warmup compatibility](https://neo4j.com/docs/apoc/current/overview/apoc.warmup/apoc.warmup.run/).

## Validation

Run `uv run pytest -q tests/seocho/test_inference_workbench.py
tests/seocho/test_schema_discovery.py`, then `bash scripts/ci/run_basic_ci.sh`.
Contract tests use synthetic data and establish no GPU performance or database
compatibility claim. Actual run reports must separately identify versions,
hardware, paid provider, skipped components and telemetry coverage.
