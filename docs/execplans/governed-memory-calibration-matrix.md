# Execute the governed-memory calibration matrix

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept current as implementation and live runs proceed. It follows `docs/maintainers/EXECPLAN_SPEC.md`.

## Purpose / Big Picture

SEOCHO needs a repeatable way to show which controls change an action, rather than merely logging that an ontology exists. After this work, an operator can run a controlled 24-case memory workload against live DozerDB and receive one content-free report per `direct`, `shadow`, and `governed` arm. The report will distinguish query admission from write/projection admission, show expected versus observed mutation outcomes, and disclose latency and missing capability rather than treating them as a pass.

The controlled corpus is calibration only. It proves the measurement path and the guardrail boundary; it does not establish external semantic generalization.

## Progress

- [x] 2026-08-22: Added `seocho.evaluation_case_envelope.v1`, coverage reporting, and a 24-case calibration generator.
- [x] 2026-08-22: Generated a 50-case external GraphRAG-Bench annotation queue with 0% ontology/triple/query reviewed coverage; it is excluded from semantic scoring.
- [x] 2026-08-22: Ran a live one-query direct/governed smoke against DozerDB and Mara MiniMax-M2.7. Both completed generation, validation, `EXPLAIN`, and execution with three rows; the governed report contains active bundle/profile and lease/fence identities.
- [ ] Add a live matrix runner that seeds two isolated workspaces and records case-level query and projection decisions.
- [ ] Run focused deterministic tests and a live DozerDB matrix with JSONL tracing.
- [ ] Record result receipts, capability gaps, cost, and a go/no-go conclusion in Beads and this plan.

## Surprises & Discoveries

- Observation: the existing Text2Cypher E2E fixture exercises one successful query and cannot by itself score the 24-case result-set corpus.
  Evidence: `scripts/benchmarks/okx_text2cypher_live.py` has one fixed question and obtains one seed intent.
- Observation: missing candidate receipt belongs to the write/projection boundary, not to online query admission.
  Evidence: `src/seocho/ontology/online_query_admission.py` only validates active bundle/profile/lease; `src/seocho/ontology/plane_policy.py` requires receipt for governed projection.
- Observation: the current live runner can emit a governed query receipt but it does not run `seochod` or a candidate projection.
  Evidence: `.seocho/e2e-live-20260822/governed.json` has a query lease receipt and no projection receipt.

## Decision Log

- Decision: report query and projection mutation results in separate fields of one case report.
  Rationale: combining them would claim a query lease proves a candidate was SHACL-receipted, which is false.
  Date/Author: 2026-08-22 / Codex
- Decision: retain `direct`, `shadow`, and `governed` as named arms without inventing a successful Rust projection when the daemon is unavailable.
  Rationale: shadow is observability-only and governed needs its declared capability; unsupported is evidence, not zero.
  Date/Author: 2026-08-22 / Codex

## Outcomes & Retrospective

The first live smoke completed on 2026-08-22 with a dedicated fixture workspace. It establishes only direct/governed query executability and query-admission attribution. The run did not use the 24-case calibration corpus, a gold result-set comparison, a judge, SHACL candidate staging, Oxigraph, or `seochod`; none of those effects are claimed.

## Context and Orientation

`scripts/benchmarks/bootstrap_governed_memory_seed.py` emits 24 local JSONL cases. A case envelope links a source snapshot, ontology terms, gold triples, required query slots/result identifiers, answer reference, and expected governance mutation. `src/seocho/eval/case_envelope.py` validates label availability and never turns `unannotated` into a score.

`direct` executes the bounded workspace-scoped query without ontology admission. `shadow` loads the active ontology profile and records a receipt but cannot make a canonicality claim. `governed` requires the active immutable bundle, profile, and short-lived lifecycle lease. A projection is a write from an approved candidate graph into DozerDB; it separately requires a semantic receipt and lifecycle admission under `src/seocho/ontology/plane_policy.py`.

## SEOCHO Evidence Contract

Each case record must contain the case receipt, arm, expected mutation, observed disposition, query result count/hash, active bundle/profile identifiers when applicable, lease generation/epoch/fence, projection decision, duration, and trace run ID. It must not contain raw document text, prompts, password, Cypher text, or result values in default artifacts.

Ontology signal is the active profile/receipt or rejection reason. Required slots and relation path come from the reviewed case envelope. Provenance is the case/source digest plus any candidate receipt. Insufficiency is an explicit `unsupported` capability or a missing gold layer. The report is the typed evidence bundle for experiment analysis; it does not synthesize prose answers.

## SEOCHO Review Panel

Professor lens: calibration data cannot prove external semantic lift; never combine it with GraphRAG-Bench answer scores. Software-engineer lens: each variant must have a deterministic expected result and preserve `workspace_id`. Computer-systems lens: live DozerDB version, host limits, cold/warm state, duration, tracing backend, and unavailable daemon must be disclosed.

Decision: promote the matrix only as a control-plane calibration run. It is falsified if a governed stale/cross-workspace case is admitted, a required receipt is treated as optional in governed projection, or output lacks case-level trace/receipt attribution.

## Cost, Latency, and Provider Policy

The admission matrix does not need an LLM. It uses a live DozerDB service and records per-case duration. A later Text2Cypher arm may use Mara MiniMax-M2.7 with fixed generation settings; a judge is secondary until its agreement with reviewed labels is measured. JSONL is the default trace artifact. No performance or provider claim is made from deterministic tests.

## Plan of Work

Implement a narrow runner in `scripts/benchmarks/` and pure aggregation helpers in `src/seocho/eval/`. The runner creates an isolated workspace pair, seeds known events, creates/activates the fixture bundle, executes each arm, and writes ignored output under `outputs/evaluation/`. It tests read admission for valid, stale, and cross-workspace cases. It tests missing receipt through projection policy without pretending to contact the Rust daemon. A later milestone adds a live `seochod` projection only when its socket is available.

## Concrete Steps

1. Add pure helpers mapping a reviewed calibration case to its workspace, query form, and expected mutation boundary. Add unit tests for every mapping.
2. Add the matrix runner with environment-variable secret reference, unique run directory, JSONL trace, direct/shadow/governed receipt, and explicit `unsupported` outcomes.
3. Run `uv run pytest -q` on the helper/runner tests, then run the matrix against live DozerDB with a generated local bundle and fixture corpus.
4. Validate output with `evaluation_case_envelopes.py`, inspect trace span counts, and update this plan and Beads with observed results.

## Validation and Acceptance

Run from the repository root:

    uv run pytest -q tests/seocho/test_case_envelope.py tests/seocho/test_governed_memory_calibration_matrix.py
    uv run python scripts/benchmarks/bootstrap_governed_memory_seed.py --output .seocho/gold/governed-memory-calibration.jsonl
    uv run python scripts/benchmarks/run_governed_memory_calibration.py --help
    bash scripts/ci/run_basic_ci.sh

For a live run, use `SEOCHO_TRACE_BACKEND=jsonl`, a unique output directory, an environment variable reference for graph credentials, and the observed DozerDB host endpoint. Acceptance requires every case to have an attributed outcome; only expected governed rejections may be called successful. A missing daemon is `unsupported`.

## Idempotence and Recovery

Every run uses a unique output and lifecycle-state directory. Fixture nodes are idempotent `MERGE` records scoped by workspace. Bundle directories are immutable; create a new directory on retry. A failed run preserves its partial report and trace. Do not delete DozerDB data outside the dedicated fixture workspaces.

## Artifacts and Notes

Generated gold, state, traces, reports, and credentials remain local below `.seocho/` or `outputs/`. Public commits contain only schemas, runner code, tests, and aggregate documentation. The Beads work item is `seocho-hr3`.

## Interfaces and Dependencies

The runner depends on Python `neo4j`, live DozerDB, `src/seocho/ontology/lifecycle.py`, `src/seocho/ontology/online_query_admission.py`, `src/seocho/ontology/plane_policy.py`, the fixture ontology builder, and vendor-neutral tracing. It does not require Mara, Oxigraph, PySHACL, or `seochod` for read-admission calibration; those dependencies must be named as unavailable or added in later explicit arms.
