# Maintainer Architecture Notes

This page keeps maintainer-facing architecture notes that were previously mixed
into the public overview. Start with `docs/ARCHITECTURE.md` for the public map.

## Why This Page Exists

`docs/ARCHITECTURE.md` should help a new user understand SEOCHO quickly. This
page keeps lower-level migration notes, priority boards, and ownership guidance
for contributors who are changing internals.

Use this page when you are touching runtime boundaries, query orchestration,
ontology contracts, or compatibility shims.

## Priority Execution Board

| Priority | Work | Current direction |
|---|---|---|
| P0 | runtime contract stability | isolate OpenAI Agents SDK calls behind an adapter and preserve response payload shape |
| P0 | real database agent provisioning | create agents only for reachable graph targets and expose degraded state |
| P1 | graph query durability | keep Cypher templates compatible with Neo4j/DozerDB warnings and `elementId()` assumptions |
| P1 | process isolation and health | separate API readiness from batch/notebook readiness |
| P1 | agent readiness state machine | normalize graph agents to `ready`, `degraded`, or `blocked` |
| P2 | governance automation | keep `/rules/assess` and ontology artifact checks as promotion gates |

Architecture changes are not done until a user can run the activation path:

1. ingest raw data through `/platform/ingest/raw`
2. ensure semantic indexes
3. ask semantic/debate questions
4. inspect trace and runtime payload
5. pass the relevant smoke gate

## Internal Orchestration Seams

Current canonical seams:

| Flow | Seam |
|---|---|
| local SDK ingest | `Seocho.add()` -> `_LocalEngine` -> `IngestionFacade` -> `IndexingPipeline` |
| runtime semantic composition | `runtime/server_runtime.py` builds `SemanticAgentFlow` through `seocho.query.AgentFactory` |
| runtime graph reads | `runtime/memory_service.py` routes reads through `seocho.query.QueryProxy` |
| runtime readiness | `runtime.agent_readiness.summarize_readiness()` normalizes agent state |

The legacy debate specialist-agent factory in `extraction/agent_factory.py`
still exists for compatibility. It is not the desired canonical factory seam
for new query behavior.

## Ontology Module Boundaries

Ontology is a first-class SDK primitive, but it should not behave as one large
implementation file.

| Module | Responsibility |
|---|---|
| `src/seocho/ontology.py` | public `Ontology`, `NodeDef`, `RelDef`, `P`, schema validation, prompt-facing API |
| `src/seocho/ontology_context.py` | ontology context hash, compact descriptors, graph-write metadata, query guardrails |
| `src/seocho/ontology_serialization.py` | JSON-LD load/save helpers |
| `src/seocho/ontology_artifacts.py` | approved artifacts, semantic prompt context, vocabulary shaping |
| `src/seocho/ontology_governance.py` | offline check, diff, export, OWL inspection |

Canonical direction:

- keep public API compatibility centered on `Ontology`
- share explicit ontology-side contracts between SDK and runtime
- keep heavy OWL and governance work out of request-time code

Related contracts:

- `docs/ONTOLOGY_RUN_CONTEXT_STRATEGY.md`
- `docs/PROPERTY_GRAPH_LENS_STRATEGY.md`

## Client Facade Boundary

`Seocho` should remain a public facade, not a second home for engine logic.

| Module | Role |
|---|---|
| `src/seocho/client.py` | public SDK facade and orchestration entrypoints |
| `src/seocho/http_transport.py` | HTTP wrapping and exception mapping |
| `src/seocho/client_artifacts.py` | ontology-to-runtime artifact bridge |
| `src/seocho/query/*` | canonical query behavior |
| `src/seocho/agent/*` | agent runtime contracts |

Keep constructor and top-level calls stable. Move transport, artifact bridge,
and local engine internals out of `client.py` over incremental slices.

## Extraction Cleanup Classification

The extraction layer is being reduced toward transport, provisioning, and
compatibility roles.

| Classification | Paths |
|---|---|
| shim now | `extraction/rule_constraints.py`, `extraction/vector_store.py` |
| compatibility alias now | `extraction/agent_server.py`, `extraction/agent_readiness.py`, `extraction/middleware.py`, `extraction/memory_service.py`, `extraction/public_memory_api.py`, `extraction/server_runtime.py`, `extraction/policy.py`, `extraction/runtime_ingest.py` |
| keep as runtime composition | `runtime/agent_server.py`, `runtime/agent_readiness.py`, `runtime/middleware.py`, `runtime/memory_service.py`, `runtime/public_memory_api.py`, `runtime/server_runtime.py`, `runtime/runtime_ingest.py` |
| shared canonical helpers | `src/seocho/index/runtime_memory.py`, `src/seocho/index/runtime_artifacts.py`, `src/seocho/index/extraction_engine.py` |

`src/seocho/index/extraction_engine.py` owns shared extraction prompt rendering,
linking prompt rendering, and graph payload normalization for SDK, compatibility
pipeline, and runtime ingest setup.

## Benchmark Direction

Measure quality in two tracks:

| Track | Purpose |
|---|---|
| private finance corpus | ingestion, graph construction, finance-domain QA |
| GraphRAG-Bench | retrieval, evidence quality, reasoning |

Measurement order:

1. SEOCHO local SDK baseline
2. SEOCHO runtime baseline
3. peer baselines

This prevents deployment overhead from being confused with canonical engine
quality. See `docs/BENCHMARKS.md`.

## Registry Patterns

### Database Registry

`DatabaseRegistry` validates database names and keeps the runtime database
allowlist.

```python
from config import db_registry

db_registry.register("mydb01")
db_registry.is_valid("mydb01")
db_registry.list_databases()
```

Name validation: `^[A-Za-z][A-Za-z0-9]*$`.

### Graph Registry

`GraphRegistry` maps `graph_id` values to database, URI, ontology, and
vocabulary descriptors.

```python
from config import graph_registry

graph_registry.list_graph_ids()
graph_registry.get_graph("kgfibo")
```

Debate mode should fan out by `graph_id`, not only by database name.

## Agent And Shared Memory Patterns

`AgentFactory` creates tools bound to a specific graph target. `SharedMemory` is
request-scoped and stores per-agent conclusions, cached query results, and
synthesis inputs.

Keep these constraints:

- create exactly one shared memory object per request
- keep graph target context bound to each tool
- do not hide connector failures as empty graph results
- report degraded state when a graph target is skipped

## Maintainer Validation

| Change | Validation |
|---|---|
| runtime shell or aliases | `bash scripts/ci/check-runtime-shell-contract.sh` |
| module ownership | `bash scripts/ci/check-module-ownership-contract.sh` |
| docs contract | `bash scripts/ci/check-doc-contracts.sh` and `python3 scripts/ci/check_doc_structure.py` |
| public site mirror | website docs checks in `website/` and `tteon.github.io` |
| shared behavior | `bash scripts/ci/run_basic_ci.sh` |

If a live dependency is unavailable, record it as a validation gap. Do not
replace a live-readiness or performance claim with mock evidence.

