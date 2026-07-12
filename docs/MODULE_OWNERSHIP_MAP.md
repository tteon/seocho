# Module Ownership Map

This page answers one engineering question directly:

> Where should new code go?

SEOCHO already has the broad architecture split documented elsewhere. This page
is the shorter contributor-facing ownership map used to keep new work out of
the wrong package.

## Canonical Rule

- `src/seocho/*` owns canonical engine behavior
- `runtime/*` owns deployment-shell behavior
- `extraction/*` is legacy batch code or compatibility surface during migration

If a change would add new business logic to `extraction/*`, it is probably
going in the wrong place.

## Ownership Table

| Concern | Canonical owner | Compatibility or legacy surface | Notes |
|---|---|---|---|
| Public SDK facade | `src/seocho/client.py`, `src/seocho/http_transport.py`, `src/seocho/client_artifacts.py`, `src/seocho/client_remote.py`, `src/seocho/client_bundle.py`, `src/seocho/local_engine.py` | none | Keep facade thin; move helper ownership into dedicated modules before adding more logic |
| Agent runtime contracts | `src/seocho/agent/*`, `src/seocho/agents.py`, `src/seocho/agents_runtime.py`, `src/seocho/runtime_contract.py` | none | Preserve OpenAI Agents SDK assumptions and vendor-neutral trace contracts |
| Ontology schema and governance | `src/seocho/ontology*.py`, `src/seocho/fibo/*` | none | Offline governance stays out of hot paths |
| Indexing and graph shaping | `src/seocho/index/*`, `src/seocho/rules.py` | `extraction/pipeline.py`, `extraction/rule_constraints.py` | Indexing, linking, runtime memory shaping, and rule logic belong here |
| Query, routing, evidence, answering | `src/seocho/query/*`, `src/seocho/prompt_strategy.py`, `src/seocho/semantic_prompt_composer.py` | none | Semantic and debate behavior should converge here |
| Runtime shell and API wiring | `runtime/*` | flat `extraction/*` aliases such as `extraction/agent_server.py` | Runtime routes, policy, readiness, and registry are deployment-shell concerns |
| Extraction compatibility and batch-only helpers | migration target varies; prefer `src/seocho/*` or `runtime/*` | `extraction/*` | Keep these as wrappers or migration surfaces, not new canonical homes |
| Benchmark harnesses and internal evaluation loops | `src/seocho/eval/*`, `src/seocho/benchmarking.py`, `evaluation/*`, `scripts/benchmarks/*` | local `.seocho/benchmarks/results/*` artifacts | Tutorial data is onboarding-only; benchmark loops use private corpora |
| Entry docs and contributor contracts | `README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/WORKFLOW.md`, `docs/ARCHITECTURE.md`, `docs/RUNTIME_ARCHITECTURE.md`, `docs/QUERY_ARCHITECTURE.md` | website mirrors | Treat these as one shared seam during edits |
| GitHub automation and CI | `.github/*`, `scripts/ci/*` | none | Keep automation slices isolated and reviewable |

## Decision Checklist

Use this quick check before editing:

1. Is this public SDK behavior or helper orchestration?
   - start in `src/seocho/*`
2. Is this runtime request validation, policy, or route composition?
   - start in `runtime/*`
3. Is this only an import shim or migration wrapper?
   - `extraction/*` is acceptable
4. Is this a one-off experiment, generated result, or local agent/tool state?
   - keep it ignored or promote it deliberately to `examples/`, `scripts/`, or
     `docs/`
5. Does the change touch multiple shared seams?
   - split the work or make the write scope explicit in the public issue or PR

## Current High-Risk Shared Seams

Most collision-prone seams are:

- SDK facade
- canonical query modules
- canonical indexing modules
- runtime shell
- entry docs
- GitHub automation

## Related Docs

- `docs/ARCHITECTURE.md`
- `docs/RUNTIME_ARCHITECTURE.md`
- `docs/QUERY_ARCHITECTURE.md`
- `docs/MAINTAINER_ARCHITECTURE_NOTES.md`
- `docs/RUNTIME_PACKAGE_MIGRATION.md`
- `docs/ISSUE_TASK_SYSTEM.md`
