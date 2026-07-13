# Remove the agent-memory sequence hot row without weakening correctness

This ExecPlan is a living document. The sections `Progress`, `Surprises &
Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to
date as work proceeds.

This plan follows `.PLANS.md` from the repository root. Beads registration was
attempted on 2026-07-13, but the shared Dolt workspace at
`/home/hadry/lab/seocho/.beads` was read-only from this worktree and could not
create its server lock. This plan is the temporary local execution record; the
public GitHub PR remains the durable work record.

## Purpose / Big Picture

SEOCHO currently assigns every authoritative memory revision in a workspace a
single increasing number by locking one `agent_memory_heads` row. The design is
easy to audit, but the live mixed workload showed atomic-write p95 increasing
from about 49 ms at concurrency 16 to about 1.49 seconds at concurrency 64.

After this work, an operator can run one benchmark that separates connection
setup, pool wait, aggregate lock, sequence allocation, SQL work, and commit
latency. The default strict ordering behavior remains compatible. Experimental
leased and sharded policies can be compared without silently treating a partial
order as the old workspace-wide total order. A small Rust prototype will use
the same PostgreSQL contract so its benefit can be attributed to runtime,
batching, and serialization rather than a weaker consistency model.

## Progress

- [x] (2026-07-13) Confirmed the live bottleneck and inspected the current
  repository: every commit opens a new psycopg connection and locks the same
  workspace head after a per-memory advisory lock.
- [x] (2026-07-13) Reviewed PostgreSQL sequence/cache, partitioning, WAL/LSN,
  lock monitoring, Psycopg pool/pipeline, Kafka partition ordering,
  CockroachDB distributed IDs/HLC, and FoundationDB versionstamp contracts.
- [x] (2026-07-13) Defined a staged plan that preserves the strict default and
  separates database-design effects from a Rust runtime effect.
- [x] (2026-07-13) Added phase-level commit telemetry, a live benchmark artifact
  schema, Prometheus export, and a Grafana phase-p95 panel.
- [x] (2026-07-13) Added an optional bounded Psycopg pool without changing the
  repository facade.
- [x] (2026-07-13) Collapsed strict head allocation to `UPDATE ... RETURNING` and avoided head
  creation on the steady path.
- [x] (2026-07-13) Introduced typed sequence-policy and causal-frontier contracts behind an
  opt-in versioned interface.
- [x] (2026-07-13) Added fenced leased and sharded experimental allocators with
  visible unused-range accounting and contract tests.
- [x] (2026-07-13) Made experimental projection acknowledgement shard-aware without
  advancing a false global watermark.
- [x] (2026-07-13) Added a Rust Tokio/SQLx prototype for pooled, bounded strict writes
  and compare it with the optimized Python path.
- [x] (2026-07-13) Ran uniform, Zipf-1.2, hot-wallet, duplicate, transition,
  reorg, projection reclaim, and recovery workloads. A new lease owner also
  proves unused reserved positions remain visible after allocator loss.

## Surprises & Discoveries

- Observation: The repository already uses a per-memory PostgreSQL advisory
  transaction lock, but it then serializes all unrelated memories through the
  workspace head row.
  Evidence: `src/seocho/memory/postgres_repository.py` calls
  `pg_advisory_xact_lock(...)` and then selects `agent_memory_heads` with
  `FOR UPDATE` for every commit.

- Observation: `PostgreSQLMemoryRepository.connect()` creates a fresh physical
  connection for every repository operation.
  Evidence: its factory is `lambda: psycopg.connect(dsn)`. The 99.03 events/s
  baseline therefore includes connection setup and must not be used as the
  pooled baseline.

- Observation: Native PostgreSQL sequences and leased ranges remove the row
  lock but allow gaps and allocation order that differs from commit order.
  Evidence: PostgreSQL documents that `nextval()` is not rolled back and cached
  values may be lost. Existing scalar projector watermarks cannot safely assume
  every lower allocated number has committed.

- Observation: Pooling and atomic allocation materially improved v1 without a
  consistency change. At 500 events/concurrency 16, throughput improved from
  275.91 to 669.17 commits/s and p95 from 129.79 to 62.59 ms.
  Evidence: `/tmp/sequence-scalability-smoke.json`, SHA-256
  `44e2a402c6577f8d7fdf7c51afab2a6d688e03860d1311314929d3adbeebfe05`.

- Observation: The bottleneck depends on key skew. At 10K uniform/64 writers,
  sequence p95 was 261.37 ms. At 5K hot-one/64 writers, aggregate-lock p95 was
  117.80 ms and sequence p95 was only 0.29 ms.
  Evidence: live PostgreSQL 18.4 artifacts documented in E-018.

- Observation: Rust reduces residual client overhead but does not remove the
  database consistency boundary. At 10K uniform it measured 875.11 commits/s
  versus Python pool 684.51; at hot-one it measured 806.46 versus 631.91.
  Evidence: both paths produced exact revision/idempotency/outbox/head parity.

## Decision Log

- Decision: Do not rewrite the current repository in Rust before removing or
  measuring the database serialization point.
  Rationale: Rust cannot parallelize transactions blocked on the same
  `FOR UPDATE` row. It is reserved for bounded async admission, local leased
  allocation, Arrow/Parquet encoding, and projector work after semantics match.
  Date/Author: 2026-07-13 / Codex

- Decision: Preserve `strict_workspace` as the default v1 behavior.
  Rationale: Existing causal tokens, point-in-time reads, outbox order, and
  watermarks depend on its total order. Experimental policies must be explicit
  and cannot reuse a scalar token with different semantics.
  Date/Author: 2026-07-13 / Codex

- Decision: Prefer aggregate-local order and bounded causal frontiers over a
  global workspace sequence for the scalable v2 design.
  Rationale: Kafka-style key partitioning and distributed databases scale by
  ordering within a partition/range. Transaction, account, and policy domains
  need strong local order; unrelated memories do not need one global cashier.
  Date/Author: 2026-07-13 / Codex

- Decision: Physical PostgreSQL table partitioning follows logical sequence
  sharding and does not substitute for it.
  Rationale: Hash-partitioning a table containing one workspace head row leaves
  all writers updating that same logical row.
  Date/Author: 2026-07-13 / Codex

## Outcomes & Retrospective

The strict v1 optimization, versioned v2 contract, live allocator, Rust parity
probe, and phase metrics are implemented. Uniform and hot-wallet runs preserved
cardinality and exposed two distinct serialization boundaries. V2 remains
experimental because full v2 revision integration and multi-process kill during
an in-flight database transaction are not complete. Lease-owner replacement,
stale projection-claim rejection/reclaim, Zipf, reorg, and projector claim
qualification have live PostgreSQL coverage.

## Context and Orientation

`src/seocho/memory/postgres_repository.py` owns the authoritative PostgreSQL
transaction. `src/seocho/memory/postgres_schema.py` owns its schema.
`src/seocho/memory/models.py` defines the scalar v1 `CausalToken`.
`src/seocho/memory/agent_projection.py` and repository outbox methods use the
sequence as a projection cursor. `scripts/benchmarks/okx_long_term_memory_mixed_load.py`
contains the workload that exposed the hot row.

A strict workspace sequence is a gapless number allocated while holding a row
lock until commit. A leased range is a block of numbers reserved by one writer;
it reduces central allocations but permits unused gaps. A sharded sequence is
monotonic only inside one deterministic shard. A causal frontier is the small
map of shard positions required by one answer, rather than a fabricated global
number. A projection watermark is the latest position that a serving graph has
durably applied for a given projection and shard.

## SEOCHO Evidence Contract

This work does not change ontology extraction or answer generation. It changes
the freshness evidence supplied to those paths. A query evidence bundle must
retain the required transaction/account identifiers, the exact revision
provenance, and either a v1 scalar causal token or a versioned v2 causal
frontier. Insufficiency must be reported as `stale` when any required shard
watermark is behind. The answerer must never infer that a numerically larger ID
means every smaller ID committed under leased or sharded allocation.

## SEOCHO Review Panel

The professor lens rejects a Rust-only rewrite because language speed does not
change the serial consistency proof. It accepts aggregate-local order if the
domain semantics and cross-domain evidence are explicit.

The software-engineer lens requires a versioned token contract, default
compatibility, migration-free opt-in prototypes, focused crash/gap tests, and a
single repository facade for pooled and unpooled connections.

The computer-systems lens requires phase spans, lock-wait evidence, uniform and
skewed keys, bounded pools, backpressure, and equal SQL semantics for the Rust
comparison. Promotion is falsified if correctness drops, if p95 merely moves
from DB lock wait to pool wait, or if the improvement disappears after Python
pooling and SQL consolidation.

The combined decision is to optimize v1 first, prototype v2 additively, and
promote Rust only after the residual client/runtime cost is measured.

## Cost, Latency, and Provider Policy

No LLM provider is needed for allocator qualification. PostgreSQL 18 is the
named live authority. DozerDB is used only when measuring projection recovery;
MARA answer calls are excluded so provider latency cannot mask storage latency.
Artifacts record service versions, concurrency, pool size, key skew, batch
size, durability settings, warmup, and every skipped dependency.

## Plan of Work

Milestone one adds a small timing observer contract to the repository. It emits
bounded phase durations and outcome labels without workspace, memory, wallet,
payload, or idempotency values as metric labels. The live runner records these
phases in its JSON artifact and existing tracing can export them.

Milestone two adds `psycopg[pool]` to the optional PostgreSQL runtime and a
`connect_pool()` constructor. The existing dependency-injected constructor and
`connect()` behavior remain supported. The strict allocator uses one atomic
`UPDATE ... RETURNING` on an existing head and initializes only on the first
workspace write.

Milestone three defines `SequencePolicy`, `CausalPosition`, and
`CausalFrontier` without changing v1 serialization. Experimental allocators
live behind benchmark/runtime configuration and use separate schema objects so
they cannot corrupt v1 workspaces. Leased allocation records owner, epoch,
range, and fencing. Sharded allocation hashes an aggregate and keeps one head
per `(workspace, domain, shard)`.

Milestone four makes experimental outbox consumption select pending rows with
bounded `SKIP LOCKED` claims and records projection progress per shard. A
frontier comparison requires every referenced shard to be current. Scalar v1
watermarks remain unchanged.

Milestone five adds a standalone Rust prototype under
`scripts/profiling/agent_memory_rust/`. Tokio provides bounded tasks and SQLx
provides a PostgreSQL pool. It invokes the same SQL function and payload as the
optimized Python runner; it does not become an SDK dependency or production
default.

Milestone six runs live matrices and updates
`docs/OKX_AI_INFRA_ENGINEERING_EVIDENCE.md`. Results separate uniform and hot
keys, throughput and tail latency, gaps and ordering, recovery, pool/lock/WAL
time, projection lag, and Python/Rust parity.

## Concrete Steps

From the repository root, run focused contract tests first:

    uv run --extra postgres pytest -q \
      tests/seocho/test_postgres_memory_repository.py \
      tests/seocho/test_memory_sequence_policy.py

Start the isolated PostgreSQL service and run the strict and experimental
matrix:

    make memory-up
    uv run --extra postgres python \
      scripts/benchmarks/agent_memory_sequence_scalability_live.py \
      --dsn "$SEOCHO_POSTGRES_DSN" --events 10000 \
      --concurrency 1,16,64,256 --policies strict,lease-128,shard-16,shard-64 \
      --distributions uniform,zipf-0.8,zipf-1.2,hot-one \
      --output /tmp/agent-memory-sequence-scalability.json

Build and run the Rust parity prototype only after the Python optimized run:

    cargo test --manifest-path scripts/profiling/agent_memory_rust/Cargo.toml
    cargo run --release \
      --manifest-path scripts/profiling/agent_memory_rust/Cargo.toml -- \
      --dsn "$SEOCHO_POSTGRES_DSN" --events 10000 --concurrency 64

Finally run repository validation:

    bash scripts/ci/run_basic_ci.sh

## Validation and Acceptance

The optimized strict path must preserve every existing revision, idempotency,
point-in-time, transition, outbox, and fencing test. Live strict cardinalities
for revisions, receipts, and outbox rows must match. No duplicate application
or missing projection event is accepted.

Experimental leased and sharded paths must preserve per-aggregate revision
order, expose rather than hide unused lease gaps, reject stale fencing, and
never advance a global scalar watermark. A causal frontier passes only when all
required shard positions have been acknowledged.

Performance acceptance is comparative rather than preselected: publish p50,
p95, p99, throughput, pool wait, sequence lock wait, SQL/commit time, and
recovery. Rust is promoted only if it improves a residual client-side boundary
over optimized Python with the same pool, SQL, durability, and sequence policy.

## Idempotence and Recovery

All schema additions use `IF NOT EXISTS` and separate experimental tables or
version columns. Benchmark workspaces and lease epochs are unique per run.
Writer-kill tests leave leases visible and allow new fenced owners to proceed;
they do not recycle IDs. Outbox consumers are idempotent and may reclaim stale
claims. The benchmark never deletes non-benchmark workspaces or Docker volumes.

## Artifacts and Notes

The starting live evidence is 99.03 events/s for the unpooled SEOCHO structured
path, 397.07 events/s for the narrower LangGraph current-value baseline, and a
mixed-workload atomic-write p95 increase from about 49 ms at concurrency 16 to
about 1.49 seconds at concurrency 64. These are baselines, not target claims.

## Interfaces and Dependencies

The planned Python interfaces are:

    PostgreSQLMemoryRepository.connect_pool(dsn, *, min_size, max_size)
    CommitPhaseObserver.record(phase: str, elapsed_ms: float, outcome: str)
    SequencePolicy.allocate(context: SequenceContext) -> CausalPosition
    CausalFrontier.satisfied_by(watermarks: Mapping[ShardRef, int]) -> bool

`psycopg[pool]` remains an optional PostgreSQL dependency. The Rust prototype
uses Tokio, SQLx, serde, sha2, and clap only inside its profiling crate. etcd,
DozerDB, and MARA are not required for the allocator microbenchmark.

## Revision Notes

2026-07-13: Created the plan after the live mixed workload identified the
workspace head row as the write-tail bottleneck and research showed that native
sequences, leased ranges, and distributed IDs change ordering semantics rather
than merely improving implementation speed.
