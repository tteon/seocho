# ADR-0111: Adopt neo4j-rust-ext (Rust PackStream codec) — §21 Gate Passed

Date: 2026-06-12

Status: Accepted

Issue: hq-5td. Methodology: ADR-0101 (profile-first gate, CLAUDE.md §21).

## Context

With multi-instance federation live (3 physical DozerDB shards,
`examples/mdm/`), the question was raised: "should we switch to the Rust
neo4j driver?" Two candidates: the official **`neo4j-rust-ext`** (Rust
PackStream codec drop-in for the Python driver; blog claims decode up to
9.27× on codec microbenchmarks) and **`neo4rs`** (Neo4j Labs pure-Rust
driver, 0.8/0.9-rc, Bolt 4.0–4.3 official, no Python bindings).

Per §21 this is a measurement, not an intuition. The A/B ran behind the real
callers, in isolated venvs (`neo4j==6.1.0` vs `neo4j-rust-ext==6.1.0.0`),
liveness-asserted per worker (`neo4j._codec.packstream.RUST_AVAILABLE`),
process-interleaved, gc-off, 5 reps + warmup, with sha256 row-hash parity.
Bench: `scripts/profiling/bench_neo4j_driver.py`; raw artifacts:
`scripts/profiling/outputs/driver_ab/`.

## Measured results (the scorecard)

| Workload | pure median | rust-ext median | Speedup | Parity |
|---|---|---|---|---|
| W1 federation read (real caller `instances_read`, 3 shards, 838 rows) | 102.8 ms | 63.5 ms | **1.62×** | OK |
| W2 bulk hydration (`yitae0530grok`, 130,064 rows, props + elementIds) | 8.157 s (15.9k rows/s) | 2.289 s (56.8k rows/s) | **3.57×** | OK |
| W2b size point (`cgamiminimaxm25`, 61k rows) | 4.029 s | 1.056 s | **3.81×** | OK |
| W2 client overhead (wall − server time) | 7,421 ms (**91% of wall**) | 1,588 ms | 4.67× | — |
| neo4rs reference ceiling (W2, release build, under-hydrated) | — | 947 ms | ~2.4× over rust-ext | not comparable |

W3 concurrency (scaling efficiency vs N=1, one federation round per agent):

| N | threads (pure) | threads (rust) | processes (pure) |
|---|---|---|---|
| 2 | 0.61 | 0.72 | 0.98 |
| 8 | 0.13 | 0.14 | 0.62 |
| 32 | 0.03 | 0.03 | 0.18 |

Pre-registered verdicts: **V1 PASS** (1.62 ≥ 1.5), **V2 PASS** (3.57 ≥ 1.5),
**V3 PASS** (exact hash parity, all workloads/reps), V4 PASS (matched pin
exists), **V7 material** (client share 91% ≥ 20%). Decision rule
(V1∨V2)∧V3∧V4 → **ADOPT**, in the V5 form below. V6 (custom Rust escalation)
**not triggered** — V1/V2 passed.

Pre-registered predictions, graded honestly (§20.4): W1 predicted 1.0–1.1× →
**measured 1.62× (prediction rejected)**; W2 whole-path predicted 1.1–1.5× →
**3.57× (rejected)** — the localhost deployment makes client-side codec cost
91% of wall (server scan ~0.7s), far above the assumed share; W3 predicted a
mild thread plateau → **severe collapse (0.13 @ N=8), rejected** — PackStream
hydration is CPU-bound Python under the GIL, and rust-ext does not change the
threading picture measurably.

Caveat: on remote deployments network time dilutes the codec share; these
ratios are upper bounds for LAN/localhost topologies (recorded, §20.8).

## Decision

1. **Adopt `neo4j-rust-ext`** as the pinned dependency replacing bare `neo4j`
   in `pyproject.toml` extras (`local`, `ci`, `dev`). Each rust-ext release
   hard-requires its matching driver version, so the pin is self-consistent.
2. **No silent activation** (§21.2): `Neo4jGraphStore.__init__` logs once per
   process which codec is live (`neo4j packstream codec: rust-ext|pure-python
   active`). Environments where the wheel is unavailable degrade to
   pure-python with the log saying so — never a guess.
3. **CI parity tripwire**: `seocho/tests/test_packstream_codec_parity.py`
   round-trips a golden value set through the installed codec (scalars,
   unicode, int-width boundaries, nested maps — the federation read's value
   families) and asserts value+type identity. It passes under both codecs.
4. **Do NOT pursue `neo4rs` / custom PyO3 driver work** (V6 not triggered):
   no Python bindings exist, the crate is rc-quality with Bolt 4.0–4.3 and no
   `element_id` accessor (§8 conflict). Its 947 ms ceiling is recorded for
   the day the calculus changes.
5. **The >100-core / Ray answer is processes, not Rust**: threads collapse to
   3% efficiency at N=32 in BOTH arms (GIL), processes hold 6× better. The
   bronze tier's one-driver-per-physical-instance design is already the right
   isolation boundary — scale-out = process/actor per shard (Ray actors map
   1:1), with rust-ext cutting per-process codec cost. The cheapest driver
   optimization remains not calling the driver: the question router
   (`examples/mdm/10_question_router.py`) short-circuits federation entirely
   for reference-data lookups (gold tier, 0.2 ms retrieval).

## Consequences

- Live MDM federation read: 1.19 s → 0.06 s end-to-end after adoption
  (same artifact: 114 proxies / 106 candidate edges — parity in production).
- Bulk paths (ER staging reads, migration, future >100-core sweeps) get
  3.5–3.8× on hydration-heavy scans for zero code change.
- Stack traces inside the codec are now native (upstream-documented trade).
- The dependency pin must move in lockstep with driver upgrades
  (`neo4j-rust-ext==X.Y.Z.*` ↔ `neo4j==X.Y.Z`); the parity test +
  liveness log catch drift.
