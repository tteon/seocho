# Module Ownership Map

This page answers one engineering question directly:

> Where should new code go?

SEOCHO already has the broad architecture split documented elsewhere. This page
is the shorter contributor-facing ownership map used to keep new work out of
the wrong package.

## Canonical Rule

- `seocho/*` owns canonical engine behavior
- `runtime/*` owns deployment-shell behavior
- `extraction/*` is legacy batch code or compatibility surface during migration

If a change would add new business logic to `extraction/*`, it is probably
going in the wrong place.

## Ownership Table

| Concern | Canonical owner | Compatibility or legacy surface | Notes |
|---|---|---|---|
| Public SDK facade | `seocho/client.py`, `seocho/http_transport.py`, `seocho/client_artifacts.py`, `seocho/client_remote.py`, `seocho/client_bundle.py`, `seocho/local_engine.py` | none | Keep facade thin; move helper ownership into dedicated modules before adding more logic |
| Ontology schema and governance | `seocho/ontology*.py` | none | Offline governance stays out of hot paths |
| Indexing and graph shaping | `seocho/index/*`, `seocho/rules.py` | `extraction/pipeline.py`, `extraction/rule_constraints.py` | Indexing, linking, runtime memory shaping, and rule logic belong here |
| Query, routing, evidence, answering | `seocho/query/*`, `seocho/prompt_strategy.py` | none | Semantic and debate behavior should converge here |
| Runtime shell and API wiring | `runtime/*` | flat `extraction/*` aliases such as `extraction/agent_server.py` | Runtime routes, policy, readiness, and registry are deployment-shell concerns |
| Extraction compatibility and batch-only helpers | migration target varies; prefer `seocho/*` or `runtime/*` | `extraction/*` | Keep these as wrappers or migration surfaces, not new canonical homes |
| Benchmark harnesses and internal evaluation loops | `seocho/benchmarking.py`, `scripts/benchmarks/*` | local `.seocho/benchmarks/results/*` artifacts | Tutorial data is onboarding-only; benchmark loops use private corpora |
| Entry docs and contributor contracts | `README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/WORKFLOW.md`, `docs/ARCHITECTURE.md` | website mirrors | Treat these as one shared seam during edits |
| GitHub automation and CI | `.github/*`, `scripts/ci/*` | none | Keep automation slices isolated and reviewable |

## Decision Checklist

Use this quick check before editing:

1. Is this public SDK behavior or helper orchestration?
   - start in `seocho/*`
2. Is this runtime request validation, policy, or route composition?
   - start in `runtime/*`
3. Is this only an import shim or migration wrapper?
   - `extraction/*` is acceptable
4. Does the change touch multiple shared seams?
   - split the work or reserve each seam explicitly through Gastown

## Current High-Risk Shared Seams

The current serialized seams are listed in:

- `.agents/gastown/shared-seams.yaml`

Most collision-prone seams are:

- SDK facade
- canonical query modules
- canonical indexing modules
- runtime shell
- entry docs
- GitHub automation

## Related Docs

- `docs/ARCHITECTURE.md`
- `docs/RUNTIME_PACKAGE_MIGRATION.md`
- `docs/GASTOWN_COORDINATION.md`
