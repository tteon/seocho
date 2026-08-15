# ADR-0156: H0 gate verdict — the two caches' working sets diverge with scale

Date: 2026-08-15 · Status: accepted (measurement record)

## Context

The unified-cache-layer plan (epic seocho-fix, design v0.3) made its own
survival conditional: H0 — "the Neo4j page cache and vLLM KV cache hold
significantly overlapping working sets" — was the top gate, to be decided in
WP0 before any joint-budget machinery was built. This ADR records the
verdict and the pivot it forces, per the repository's rule that ontology and
cache experiments land with their data.

## Method

The 702-episode FinBench agent<->database run (#462/#478) stored every
call's Cypher, so the workload replays without a model in the loop.
Against reloaded `sf1-layers` / `sf10-layers` graphs
(graphstack/dozerdb:5.26.3.0):

- **DB-side working set**: per call, a shadow query returns `elementId` for
  every node variable the MATCH..WHERE clause binds (`scripts/cache_sim/
  collect_finbench_traces.py`). A lower bound on the page working set —
  DozerDB CE exposes no page identities (measured: no metrics subsystem, no
  `org.neo4j` MBeans), so index pages and scanned-but-unbound entities are
  invisible. That bias *raises* measured overlap, which makes a FAIL robust.
- **KV-side working set**: nodes whose identity survives into the serialized
  context — RETURN-exposed variables outside aggregates, capped at the row
  cap the call actually ran with. A `count()` row carries no node into the
  KV cache.
- **Metrics**: Jaccard of the universes and of the top-decile
  (episode-frequency) hot sets; threshold 0.30 set here — below it the hot
  sets are mostly disjoint and there is nothing shared to co-manage. H1 uses
  the plan's own threshold (top-decile nodes < 30% of appearances → reject
  pin/quantization).

Raw numbers: `ADR-0156-h0-gate-verdict.json`. Coverage caveats recorded
there: 154/234 episodes per scale carry read sets (WITH-scope shadows are
refused rather than guessed); single-anchor workload; ORDER BY dropped in
capped context shadows.

## Results

| | SF1 | SF10 |
|---|---|---|
| DB universe (nodes bound) | 2,850 | 22,617 (**8.0x**) |
| KV universe (nodes in context) | 736 | 1,157 (1.6x) |
| H0 Jaccard, full universes | 0.258 | 0.051 |
| **H0 Jaccard, top-decile hot sets** | **0.226 — FAIL** | **0.050 — FAIL** |
| Containment KV ⊆ DB | 1.000 | 1.000 |
| H1 top-decile appearance share | 0.238 — FAIL | 0.283 — FAIL, rising |
| MRC hit rate @1024 blocks (session/shuffled) | 57.6% / 28.8% | 36.0% / 19.9% |

The working sets do not overlap and then drift — they **diverge**: the DB
side grows with the graph while what reaches the LLM stays an anchor-centric
slice bounded by row caps and aggregates. Containment 1.0 says the KV set is
a small subset of the DB set, not an overlapping peer.

The KV side has its own structure worth keeping: the WP0 simulator (#487)
measures large shared-prefix block counts and a 2x hit-rate gap between
session-clustered and shuffled arrivals — prefix reuse is real and
exploitable without any joint budgeting.

## Decision

Per the plan's own Go/No-Go table (§4.2, "Jaccard 중첩 낮음 → 통합 계층
폐기, 두 캐시 독립 최적화로 전환"):

1. **Dropped**: WP3 joint budget allocation and cross-prefetch
   (opportunities A and B). seocho-fix.5 stays open only pending an SF100
   confirmation run; no work starts on it.
2. **Kept**: WP4 derived-KV invalidation — a correctness property that never
   depended on overlap; WP2 KV-side canonicalization/ordering — justified by
   the measured prefix reuse; pattern-trace experiments (fix.11).
3. **Narrowed**: the ADR-0155 Rust data plane invokes its own kill-criteria
   clause — scope shrinks to the invalidation + observation proxy.
4. **H1 is not closed**: the share rises with scale (0.238 → 0.283); rerun
   at SF100 before deciding pin/quantization policy.

## Consequences

Engineering weight shifts to the product-feature track (observability, CLI,
ontology import — seocho-5bg) and to the two surviving cache workstreams.
The negative result itself is the WP0 characterization deliverable the plan
pre-registered ("why text-RAG caching techniques do not transfer to graphs")
and is written to stand alone.
