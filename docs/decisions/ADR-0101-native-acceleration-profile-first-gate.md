# ADR-0101: Native Acceleration (Rust/PyO3) Profile-First Gate

Date: 2026-06-03

Status: Accepted

## Context

A dormant Rust crate, `seocho-core/` (PyO3 0.23 cdylib), was found in the repo
exposing three `#[pyfunction]`s — `cosine_similarity`, `cosine_similarity_matrix`,
and `infer_rules_from_nodes` — wired into `seocho/index/linker.py` and
`seocho/rules.py` behind `try: import seocho_core` guards. It had **never been
built or installed** (`import seocho_core` failed), so production ran the pure-
Python fallbacks. A latent bug explained the dormancy: `src/lib.rs` declared the
pymodule as `fn _native` while the lib name and all call sites expected top-level
`seocho_core`, so even a successful build would not import (`PyInit_seocho_core`
missing).

The open question was the recurring one: **which parts of SEOCHO are worth
rewriting in / porting to Rust?** Rather than answer by intuition, we treated it
as a measurement: fix the crate enough to build, then benchmark honestly at $0
(no LLM/embedding API — the user bears all API costs) and decide from data.

## Decision

Establish a **mandatory profile-first gate** for all native-acceleration work
(codified as CLAUDE.md §21). A candidate may only be activated/ported if it
passes, measured: (1) whole-path A/B behind the real caller, (2) a marshaling-
isolated baseline (a `noop_consume` probe) to separate boundary cost from
compute, (3) a material Amdahl share, (4) a win over the optimized incumbent
(NumPy/BLAS, `orjson`, `tokenizers` — adopt existing Rust libs before writing
custom PyO3), and (5) a deterministic, Python-identical cross-impl golden parity
test. Native paths must be gated behind an explicit OFF-by-default env flag —
never silently activated by a bare `try: import`.

For the `seocho-core` crate specifically: **keep it dormant; do not activate any
of the three functions.** The Python fallback remains the production path.

## Measured results (the scorecard)

$0 benchmark (`scripts/profiling/bench_seocho_core.py`): synthetic 1536-dim f64
vectors + real node JSON from existing DozerDB graphs; interleaved A/B, GC off,
warmup, min/median/p90.

| Candidate | Measurement | Verdict | Mechanism |
|-----------|-------------|---------|-----------|
| `cosine_similarity` (dim 1536) | 4.58× wall, but **69% of the call is PyO3 marshaling** (20.2µs of 29.4µs); native compute ~9.3µs | **Skip** | Called once per record behind a ~200ms embedding network call → Amdahl share ~0%. Marshaling ≈ compute. |
| `cosine_similarity_matrix` (N×N) | rust 5.2/72/255ms vs **NumPy(BLAS) 0.3/24/57ms (3–18× slower)** at N=64/256/512 | **Drop/quarantine** | Naive triple-loop vs tuned BLAS GEMM; also called from nowhere. |
| `infer_rules_from_nodes` (whole path) | 0.69×/1.01×/1.27×/0.90× at 100/1k/4k/10k nodes ≈ **no win** | **Skip** | Native compute offset by double JSON round-trip (`dumps`→serde→serialize→`loads`). |

Additional correctness finding: `infer_rules_from_nodes` is **non-deterministic**.
Rust `infer_dominant_type` uses `HashMap<&str,usize>` + `max_by_key`; on a type-
count tie the winner depends on `HashMap` iteration order (per-process
`RandomState`). Five repeated runs gave datatype-rule diffs of `[0,1,1,1,1]` —
i.e. activating it changes rule output run-to-run, violating reproducibility
(§20.7). Because the activation guard was a bare `try: import`, merely installing
the `--user` wheel flipped the live environment onto this non-deterministic path
(§18 silent-fallback violation); the wheel was uninstalled immediately to restore
the measured-safe fallback.

## Consequences

- `seocho-core` stays dormant; Python fallback is the production path (also
  deterministic and as-fast-or-faster on rules, marshaling-free on cosine).
- Source fixes retained (they are strictly better than the broken state): the
  `_native`→`seocho_core` pymodule rename, the int/float parity-direction fix in
  `rules.rs` (classify by JSON literal form to match Python `_type_name`), the
  `noop_consume` marshaling probe, and the `pyproject.toml` single-module layout.
- **Preconditions before the crate may ever be reinstalled near live:** (a)
  replace `try: import` guards with an explicit OFF-by-default env flag; (b) make
  the rules tie-break deterministic and Python-matching; (c) add a cross-impl
  golden parity test in CI. The `cosine_similarity_matrix` is reference-only.
- **Methodology mandate (durable):** PyO3 candidates require a marshaling-
  isolated baseline AND a whole-path A/B behind the real caller before any
  activation. Inner-function wall-clock ratio is not sufficient evidence.

## Meta-lesson

The gate did its job: it stopped a Rust path that would have added an FFI
maintenance boundary, a build/release dependency, and a non-deterministic
correctness regression for ~0 measured end-to-end benefit. The single highest-
leverage artifact was the `noop_consume` probe, which converted an impressive-
looking "4.58× cosine" into the true verdict (marshaling-dominated, Amdahl-
irrelevant).

## Follow-up

Profile the offline data plane to find the actual hotspot; evaluate **`orjson`
for JSONL/artifact IO first** (zero custom Rust) before considering any new PyO3,
then chunking/tokenization only if measured hot. Do not pre-commit to a target.
