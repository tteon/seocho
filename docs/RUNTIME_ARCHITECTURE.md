# Runtime Architecture

This page explains the HTTP runtime and local service boundary. Read
`docs/ARCHITECTURE.md` first if you only need the product overview. Read
`docs/RUNTIME_DEPLOYMENT.md` if you only want to run the stack.

## Runtime In One Page

SEOCHO has one canonical engine under `src/seocho/` and one deployment shell
under `runtime/`.

| Layer | Owns | Main paths |
|---|---|---|
| SDK engine | ontology, indexing, query, answer contracts | `src/seocho/` |
| Runtime shell | HTTP routes, request policy, readiness, service composition | `runtime/` |
| Compatibility shell | legacy imports and batch compatibility | `extraction/` |
| UI shell | local evaluation frontend and proxy | `evaluation/` |

The runtime should not become a second copy of the engine. When a route needs
canonical behavior, it should call the SDK/query/indexing seam rather than
reimplementing that behavior in a FastAPI file.

## Local Stack

The supported local stack is intentionally small:

| Service | Purpose |
|---|---|
| `neo4j` | local graph database, compatible with DozerDB-style Cypher workflows |
| `extraction-service` | runtime API container name kept for compatibility |
| `evaluation-interface` | local chat and inspection UI |

`extraction-service` is still the compose service name, but the long-term code
owner is `runtime/`. The default image bakes `extraction/`, `runtime/`, and
`src/seocho/` into one known source snapshot. Live bind mounts are reserved for
explicit development loops such as `make up-live` or `make dev-up`.

## Composition Root

Runtime service composition should start in `runtime/server_runtime.py`.

| Concern | Owner |
|---|---|
| route registration and request validation | `runtime/agent_server.py` |
| shared runtime service construction | `runtime/server_runtime.py` |
| memory-first ingest and search facade | `runtime/memory_service.py` |
| raw text runtime ingestion | `runtime/runtime_ingest.py` |
| request policy | `runtime/policy.py` |
| agent readiness summary | `runtime/agent_readiness.py` |
| request ID propagation | `runtime/middleware.py` |
| public memory routes | `runtime/public_memory_api.py` |

Routers should prefer lazy service getters over eager singleton boot at import
time. This keeps tests, local development, and degraded startup states easier to
reason about.

## Runtime Package Direction

`extraction/` is a historical name. New runtime-facing behavior should move
toward this package shape:

| Package | Direction |
|---|---|
| `src/seocho/` | canonical SDK and engine modules |
| `runtime/` | deployment shell, HTTP routes, policy, readiness, registries |
| `extraction/` | extraction helpers and compatibility wrappers during migration |

Use `docs/RUNTIME_PACKAGE_MIGRATION.md` for the staged migration contract.
Use `docs/MODULE_OWNERSHIP_MAP.md` when deciding whether a change belongs in
`src/seocho/`, `runtime/`, or `extraction/`.

## API Surface

| Endpoint | Purpose |
|---|---|
| `POST /run_agent` | legacy router mode |
| `POST /run_agent_semantic` | semantic graph QA mode |
| `POST /run_debate` | multi-graph debate mode |
| `POST /indexes/fulltext/ensure` | ensure graph fulltext index exists |
| `POST /platform/chat/send` | local platform chat endpoint |
| `POST /platform/ingest/raw` | raw record ingestion into a graph target |
| `GET /platform/chat/session/{session_id}` | read platform session history |
| `DELETE /platform/chat/session/{session_id}` | reset platform session |
| `POST /semantic/artifacts/drafts` | save draft ontology/SHACL/vocabulary artifacts |
| `GET /semantic/artifacts` | list semantic artifacts |
| `GET /semantic/artifacts/{artifact_id}` | read one semantic artifact |
| `POST /semantic/artifacts/{artifact_id}/approve` | promote a draft artifact |
| `POST /semantic/artifacts/{artifact_id}/deprecate` | deprecate an approved artifact |
| `GET /databases` | list registered databases |
| `GET /agents` | list active database-bound agents |

The user activation path for runtime changes is:

1. ingest raw data through `/platform/ingest/raw`
2. ensure the fulltext index through `/indexes/fulltext/ensure`
3. ask through `/platform/chat/send`
4. inspect trace and response metadata
5. pass the runtime smoke gate

## Readiness And Degraded States

The runtime must distinguish "API process is up" from "every graph agent is
ready."

| State | Meaning | Runtime behavior |
|---|---|---|
| `ready` | target graph and tools are usable | route normally |
| `degraded` | some graph targets are missing or unavailable | return partial state and avoid unsafe fan-out |
| `blocked` | required target or policy gate is unavailable | fail with an explicit reason |

Debate mode should only create agents for graph targets that exist and are
reachable. If some targets are skipped, the response payload should expose that
state instead of pretending the comparison was complete.

## Storage And Artifacts

| Artifact | Location |
|---|---|
| local graph state | `data/neo4j/` |
| semantic artifacts | `outputs/semantic_artifacts/` |
| rule profile registry | `outputs/rule_profiles/rule_profiles.db` |
| semantic run metadata | `outputs/semantic_metadata/` |
| JSONL traces | `SEOCHO_TRACE_JSONL_PATH` |

See `docs/FILES_AND_ARTIFACTS.md` for inspection commands.

## Observability

SEOCHO supports a vendor-neutral trace contract:

| Backend | Use |
|---|---|
| `none` | disable trace export |
| `console` | local debugging |
| `jsonl` | durable local evidence |
| `opik` | team evaluation and span inspection |

Opik is optional. The runtime should still be explainable through JSONL traces
and response metadata when Opik is disabled.

## Frontend Trace Vs Opik

| Surface | Role |
|---|---|
| local platform UI | interactive chat, candidate override loop, raw ingest controls |
| runtime response payload | request result, trace steps, readiness metadata |
| JSONL trace | portable evidence for local runs and CI artifacts |
| Opik | optional team-grade evaluation, span trees, cost and latency inspection |

Do not make a feature depend on the frontend trace view alone. The runtime
payload or trace artifact should carry the same operational evidence.

## Configuration

Common environment variables:

```bash
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password

SEOCHO_TRACE_BACKEND=none
SEOCHO_TRACE_JSONL_PATH=./traces/seocho-runtime.jsonl
SEOCHO_TRACE_OPIK_MODE=self_host
OPIK_URL=http://opik-backend:8080
OPIK_WORKSPACE=default
OPIK_PROJECT_NAME=seocho
OPIK_API_KEY=
```

The runtime should stay environment-first. Reference YAML under
`extraction/conf/` is useful for compatibility and examples, but runtime
deployment should not require editing source-controlled config files.

## Validation

Run the narrowest relevant check first:

| Change | Validation |
|---|---|
| runtime routes, policy, readiness | `bash scripts/ci/check-runtime-shell-contract.sh` |
| runtime docs only | `bash scripts/ci/check-doc-contracts.sh` |
| behavior or API payload shape | `bash scripts/ci/run_basic_ci.sh` |
| public runtime guide | website docs checks after mirroring |

Mocks can validate contracts and deterministic failures. They are not evidence
for throughput, latency, scalability, or production readiness.

