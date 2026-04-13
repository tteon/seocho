# ADR-0056: Canonicalize SemanticAgentFlow into the SDK

**Date:** 2026-04-13
**Status:** Accepted
**Authors:** hadry

## Context

`extraction/semantic_query_flow.py` (2,595 LOC, 15 classes) holds the entire
semantic query orchestration: entity resolution, intent inference, query
routing, LPG/RDF agents, repair loop, answer synthesis. The local SDK has
only partial equivalents (`seocho/query/{planner,executor,answering}.py`)
and no `SemanticAgentFlow` analogue.

This is the largest remaining local↔server parity gap. The parity harness
at `tests/test_parity_harness.py` cannot exercise semantic queries on the
local path because the orchestration does not exist there.

A previous instinct was to keep this in `extraction/` because it touches
DB-stateful operations (fulltext entity lookup, multi-database constraint
loading). A survey of leading knowledge-graph SDKs disproves this:

| Solution | Where the orchestration lives |
|----------|-------------------------------|
| Graphiti (Zep) | `graphiti_core.Graphiti` — `server/graph_service/routers/retrieve.py` is a thin FastAPI wrapper that delegates to `graphiti.search()` etc. |
| Cognee | `cognee.recall()` — same code path for local and `cognee.serve()` hosted mode |
| mem0 (graph) | `Memory` class — graph store is just a config option, no separate orchestration class |
| LlamaIndex | SDK only — user brings the DB |
| Neo4j GraphRAG | SDK only — `Driver + Retriever + LLM` triple |

**Every leading solution puts DB-stateful query orchestration in the SDK.**
None split it into a server-only module. The "DB-stateful = server-only"
assumption is incorrect — the SDK simply needs `GraphStore` and
`LLMBackend` abstractions, both of which `seocho/` already has.

## Decision

Move `SemanticAgentFlow` and its supporting classes into the SDK as the
canonical query orchestration. `extraction/agent_server.py` becomes a
FastAPI thin wrapper that calls the SDK class — matching the Graphiti
pattern.

## Target Structure

```
seocho/query/                         ← canonical
├── semantic.py                       ← SemanticAgentFlow (new)
├── intent.py                         ← IntentSpec, IntentSupportValidator,
│                                       infer_question_intent (already partial)
├── strategy_chooser.py               ← ExecutionStrategyChooser
├── insufficiency.py                  ← QueryInsufficiencyClassifier
├── cypher_validator.py               ← CypherQueryValidator
├── constraints.py                    ← SemanticConstraintSliceBuilder
├── entity_resolver.py                ← SemanticEntityResolver (uses GraphStore)
├── router.py                         ← QueryRouterAgent
├── lpg_agent.py                      ← LPGAgent (uses GraphStore + LLMBackend)
├── rdf_agent.py                      ← RDFAgent
├── answering.py                      ← AnswerGenerationAgent (extends existing)
└── run_registry.py                   ← RunMetadataRegistry

extraction/semantic_query_flow.py     ← thin re-export shim
extraction/agent_server.py            ← /run_semantic delegates to seocho.query.semantic
```

## Migration Phases

Each phase is independently shippable and gated by the parity harness.

### Phase A: Pure-logic classes (no DB) — low risk
Move classes that only consume data, no DB calls:
- `IntentSupportValidator`
- `ExecutionStrategyChooser`
- `QueryInsufficiencyClassifier`
- `CypherQueryValidator`
- `IntentSpec`, `CypherPlan`, `InsufficiencyAssessment`

Target files: `seocho/query/intent.py`, `strategy_chooser.py`,
`insufficiency.py`, `cypher_validator.py`, `contracts.py` (extend).

### Phase B: DB-aware support classes — medium risk
Move classes that read from DB but don't orchestrate:
- `SemanticConstraintSliceBuilder` (reads artifacts via GraphStore)
- `RunMetadataRegistry` (writes audit records)

Refactor to use `GraphStore` abstraction instead of direct connector.

### Phase C: Agents — medium risk
Move the per-route agents:
- `SemanticEntityResolver` (uses `graph_store.query()` for fulltext)
- `QueryRouterAgent` (pure routing)
- `LPGAgent`, `RDFAgent` (uses `graph_store.query()` + `llm.complete()`)
- `AnswerGenerationAgent` (extend existing `seocho/query/answering.py`)

### Phase D: SemanticAgentFlow — final integration
- Move `SemanticAgentFlow` to `seocho/query/semantic.py`
- `extraction/semantic_query_flow.py` becomes re-export shim
- `extraction/agent_server.py` `/run_semantic` handler delegates to
  `seocho.query.semantic.SemanticAgentFlow().run(...)`
- Add parity harness assertion: local SDK semantic query produces the
  same `SemanticAgentResponse` shape as the server endpoint

## Acceptance Gate

Each phase must keep:
- `tests/test_parity_harness.py` 9 passed, 0 xfail
- `extraction/tests/test_api_endpoints.py` all passing
- New unit tests for moved classes in `seocho/tests/`

Phase D adds a new parity assertion: same ontology + question produces
the same `route`, `support_assessment`, `evidence_bundle` from both paths.

## Out of Scope

- Deprecation of `extraction/semantic_query_flow.py` shim (keep
  indefinitely for HTTP-mode external consumers)
- Rewriting `LPGAgent` / `RDFAgent` business logic — only relocate +
  rewire to use SDK abstractions
- Caching/perf improvements (separate lane)

## Risks

| Risk | Mitigation |
|------|------------|
| `connector` parameter (Neo4jConnector) doesn't match `GraphStore` interface | Phase B includes adapter layer; SDK already has `Neo4jGraphStore` |
| 15 classes spread across 12 files = large surface | Phased approach; each phase < 500 LOC moved |
| Other agent working on `runtime_ingest.py` may touch shared code | Coordinate via beads; this ADR's phases avoid `runtime_ingest.py` |
| HTTP API contract change | None — `agent_server.py` keeps the same endpoint, just delegates internally |

## References

- Graphiti: <https://github.com/getzep/graphiti> (`graphiti_core/` + thin `server/`)
- Cognee: <https://github.com/topoteretes/cognee> (`cognee.serve()` pattern)
- mem0: <https://docs.mem0.ai/open-source/graph_memory/overview>
- ADR-0048: canonical query engine first slice (precedent)
- ADR-0055: runtime ingest canonical extraction seam (precedent)
