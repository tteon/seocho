# ADR-0182: indexing parallelism — concurrent extraction + shared intern table

Date: 2026-08-16 · Status: accepted (measured) · seocho-ia4 (indexing parallelism)

## Context

hadry: indexing has parallelizable parts — implement in Rust, multi-thread, from a
shared-memory perspective (SEOCHO aims to be an OS). We separated the kinds of
parallelism and let measurement decide where each tool belongs, in four steps.

## Decision & result (the four steps)

**Step 1 — concurrent extraction (I/O-bound → threads, not Rust).** Per-chunk LLM
extraction is a network round-trip, independent across chunks. `index/parallel.py::
concurrent_map` (order-preserving, exception-capturing thread pool) pre-fetches all
chunk extractions concurrently; the deterministic post-processing loop is unchanged
and still runs in chunk order. Opt-in via `SEOCHO_EXTRACTION_CONCURRENCY` /
`_extraction_concurrency` (default 1 = exact back-compat). A per-chunk failure is
captured in place and re-raised into the existing strict/guided fallback handler —
so behavior is identical to sequential. Existing index/pipeline/extraction tests
pass **identically** with concurrency off (151) and on (=4, 151).

**Step 2 — profile (measure before optimizing).** `scripts/agentos/
bench_indexing_concurrency.py` (deterministic, mock latency):

| extraction (12 chunks @ 0.4s, I/O-bound) | wall | speedup |
|---|---|---|
| 1 worker | 4.81s | 1.0× |
| 4 | 1.20s | 4.0× |
| 8 | 0.80s | **5.99×** |
| 12 | 0.40s | 11.94× |

Interning (CPU-bound): **1.19M ops/s** — interning a whole doc's entities ≈ 0.5 ms,
negligible beside a multi-second extraction.

**Step 3 — shared-memory intern table (the OS shared-memory core).**
`index/shared_intern.py::SharedInternTable` — a process-wide, thread-safe,
workspace-scoped (protection-domain-keyed), sharded (per-shard lock) map from a
composite identity to its canonical id. First-writer-wins, so the same entity
interned on N threads converges to ONE canonical address (proven: 16 threads racing
one entity → one id, zero fragmentation). This is hadry's "shared memory" made
concrete — the allocator's canonical-entity namespace shared across concurrent
extraction workers / agents. **Built in Python.**

**Step 4 — Rust only when measured-warranted (don't premature-optimize).** The
profile settles it with data: extraction is I/O-bound (a thread pool wins ~6–12×;
Rust buys nothing for network waits), and interning does 1.2M ops/s (CPU is nowhere
near the bottleneck). **A Rust `seocho-core` concurrent intern table is NOT warranted
now.** Documented escalation trigger: revisit when interning throughput matters
(< ~50k ops/s under real load, or the CPU tail — dedup + axiom mining + cosine —
becomes a measured share of indexing wall-clock at scale). SEOCHO already has a
`seocho-core` Rust crate (cosine/rules) + rust-ext codec, so the escalation path is
short when the data calls for it.

## Consequences

- The big indexing win (overlapping LLM round-trips) is delivered, opt-in, safe, and
  measured (~6× at 8 workers). The shared-memory intern table gives correct
  cross-thread interning (no fragmentation) for when concurrency is enabled.
- Composes with the OS concurrency discipline: the shared intern table is the
  shared-memory structure the RCU/EBR reclamation work (ia4.3/.4) governs.
- Follow-ups: wire `SharedInternTable` into the pipeline's identity step under
  concurrency; async (vs thread) extraction if the sync client becomes a limit;
  Rust intern table iff the escalation trigger fires. +7 tests
  (`test_indexing_parallel.py`).
