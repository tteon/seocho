# Run SDCR as a live blockchain-memory evidence swarm

This ExecPlan is a living document. The sections `Progress`, `Surprises &
Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to
date as work proceeds.

This plan follows `.PLANS.md` from the repository root and is tracked by Beads
item `seocho-vu4`.

## Purpose / Big Picture

SEOCHO can now select graph views with SDCR, but selection alone does not prove
that multiple agents can cooperate. After this work, a query can declare the
answer slots it needs, route to the smallest authorized specialist coalition,
execute those specialists concurrently, filter protected evidence, expose
conflicts and missing slots, and pass one typed evidence bundle to answer
synthesis. An operator can run the same path against the populated PostgreSQL
blockchain long-term-memory workspace and inspect the run in Grafana/Tempo.

## Progress

- [x] (2026-07-13) Audited the SDCR slice and live mixed-memory workload.
- [x] (2026-07-13) Confirmed the service stack and found a
  1,003,595-revision workspace.
- [x] (2026-07-13) Added the typed evidence-swarm coordinator and contract tests.
- [x] (2026-07-13) Added and ran the live PostgreSQL mixed workload with three
  sampled MARA answers.
- [x] (2026-07-13) Passed 37 focused tests, 655-test Basic CI, live Tempo and
  Prometheus verification, and updated the single engineering-evidence record.

## Surprises & Discoveries

- Observation: `SDCRRouter` selects views and emits a receipt, but no product
  component executes those views or assembles their evidence.
  Evidence: `src/seocho/query/sdcr.py` contains routing and evidence helpers.
- Observation: SEOCHO already has `AgentCapability`, `Matchmaker`, and
  `AgentExchange`; the missing seam is coalition execution, not another
  general-purpose agent framework.
  Evidence: `src/seocho/agent/matchmaker.py` and
  `src/seocho/agent/exchange.py`.
- Observation: thread-pool fan-out initially created hundreds of independent
  Tempo roots, and the report's trace ID addressed SEOCHO's fallback UUID
  rather than the native OTel trace.
  Evidence: the corrected final run stores 2,139 spans under one root trace,
  `9cad771d4c6b3d4c949dae8de231dc34`.

## Decision Log

- Decision: Implement an evidence-swarm coordinator, not answer debate.
  Rationale: the handoff contract requires one typed evidence bundle;
  independent prose debate does not fill missing slots.
  Date/Author: 2026-07-13 / Codex
- Decision: Keep specialist execution provider-neutral and compatible with
  OpenAI Agents SDK adapters and deterministic data specialists.
  Rationale: storage latency must not be hidden by provider latency.
  Date/Author: 2026-07-13 / Codex
- Decision: Separate high-concurrency deterministic synthesis from a small
  live MARA answer sample.
  Rationale: this attributes orchestration latency while proving final LLM I/O.
  Date/Author: 2026-07-13 / Codex

## Outcomes & Retrospective

The coordinator and live runner are implemented and repository validation is
complete. The final 2,000-operation run
at concurrency 32 completed at 892.67 ops/s with zero unexpected errors. It
executed 596 two-/three-specialist queries with zero missing slots or conflicts,
filtered 596 protected payloads, preserved 1.004M-row authority integrity, and
drained ten projection events in 353 ms. Three MARA answers completed. Tempo
contains one root and 2,139 descendant spans; Prometheus contains 599 complete
coordinator requests. Basic CI passed with 655 tests and three skips.

## Context and Orientation

`src/seocho/query/sdcr.py` owns deterministic slot-based coalition selection.
`src/seocho/tracing.py` and `src/seocho/metrics.py` own vendor-neutral traces
and bounded production metrics. The new coordinator belongs in
`src/seocho/query/` because it converts query intent into an evidence bundle.
`scripts/benchmarks/okx_long_term_memory_mixed_load.py` is the existing live
storage workload that this work extends rather than replaces.

A specialist is an authorized query worker with declared output slots. A
coalition is the smallest set selected to cover required slots. Evidence swarm
means specialists collect evidence in parallel and produce one bundle; it does
not mean models debate prose.

## SEOCHO Evidence Contract

Every result carries intent, required slots, an SDCR receipt, specialist runs,
safe evidence, slot fills, missing slots, conflict candidates,
protected-evidence count, provenance, and partial/complete status. The answerer
receives only safe evidence. Missing slots and conflicts stay visible.

For the blockchain workload, slots cover authoritative current state,
point-in-time state, projection state/freshness, and provenance. The relation
path is the memory revision chain and outbox/projection mapping. Provenance is
the revision provenance identifier and causal sequence.

## SEOCHO Review Panel

The professor lens accepts multiple specialists only because they fill
different slots with inspectable provenance; it rejects generic answer debate.

The software-engineer lens requires immutable typed contracts, workspace
propagation, declared-slot validation, deterministic ordering, explicit partial
results, and focused failure tests.

The computer-systems lens requires bounded fan-out, per-specialist latency,
timeouts, low-cardinality metrics, and separate storage/MARA latency. The
design is falsified if parallel execution loses evidence or hides failures.

## Cost, Latency, and Provider Policy

PostgreSQL 18.4 is the live authority. The load path uses no LLM. MARA is used
only for a bounded answer sample and records model, tokens, latency, and status
without persisting prompts or raw completions. OTel exports to the existing
collector; metric labels contain no workspace, wallet, transaction, prompt, or
payload values.

## Plan of Work

Add `src/seocho/query/evidence_swarm.py` with request/result contracts and a
coordinator that calls SDCR, runs workers in a bounded thread pool, validates
declared slots, filters protected evidence, detects conflicts, emits
traces/metrics, and invokes an optional synthesizer. Export the contracts from
`seocho.query`.

Add deterministic tests and a benchmark combining the storage mix with
current, historical, projection, and provenance specialists against live
PostgreSQL. Run a small MARA sample after load and write a content-safe report
outside the repository.

Update ADR-0152, the decision log, this handoff spec, and
`docs/OKX_AI_INFRA_ENGINEERING_EVIDENCE.md` with measured evidence only.

## Concrete Steps

From the repository root:

    uv run --extra dev pytest -q tests/seocho/test_evidence_swarm.py
    uv run --extra postgres --extra otel python \
      scripts/benchmarks/okx_multi_agent_memory_workload.py \
      --dsn "$SEOCHO_POSTGRES_DSN" --workspace ltm-scale-1m-20260712 \
      --operations 2000 --concurrency 32 --llm-cases 3 \
      --output /tmp/okx-multi-agent-memory-live.json
    bash scripts/ci/run_basic_ci.sh

## Validation and Acceptance

Tests prove concurrent execution, authorization, protected-data removal,
conflict visibility, failure partial results, and workspace propagation. The
live report identifies the populated workspace and PostgreSQL version,
preserves authority integrity, records non-zero multi-specialist coalitions,
zero protected-evidence leaks, latency percentiles, and successful sampled
MARA answers or an explicit provider gap. Traces and metrics are queryable from
Tempo and Prometheus/Grafana.

## Idempotence and Recovery

The workload uses unique run identifiers for new revisions and idempotency
keys. Re-running creates a separate run. Projection refreshes advance only
after committed rows. MARA failure does not invalidate storage correctness and
is reported separately. Reports stay under `/tmp` unless a summary is promoted.

## Artifacts and Notes

The initial inventory found workspace `ltm-scale-1m-20260712` with 1,003,595
revisions and 336,929 memories. The live run will record service versions and
final counts.

## Interfaces and Dependencies

The coordinator accepts specialists implementing `capability: Capability` and
`retrieve(request) -> Sequence[Evidence]`. `run(request, synthesizer=None)`
returns a typed receipt and evidence bundle. It uses standard-library
concurrency, SDCR, tracing, and production metrics. The live benchmark uses the
optional `postgres`, `otel`, and `openai` dependencies.

Revision note (2026-07-13): Created after auditing the merged SDCR slice and
confirming live service/data inventory.

Revision note (2026-07-13): Updated with final PostgreSQL/MARA measurements and
the trace-context/native-ID fixes found during live qualification.
