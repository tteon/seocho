# SEOCHO Architecture Overview

SEOCHO turns unstructured data into ontology-aligned graph memory. This page is
the public architecture overview: it explains the product boundary, the main
planes, and where to go for deeper implementation detail.

Do not start here if you only want a first run. Use `QUICKSTART.md` or
`docs/RUNTIME_DEPLOYMENT.md` first.

## Reader Guide

| If you need... | Read this page until... | Then read |
|---|---|---|
| the product shape | [Architecture In One Page](#architecture-in-one-page) | [Why SEOCHO](WHY_SEOCHO.md) |
| the runtime internals | [Runtime Boundary](#runtime-boundary) | [Runtime Architecture](RUNTIME_ARCHITECTURE.md) |
| query and Graph-RAG internals | [Query Boundary](#query-boundary) | [Query Architecture](QUERY_ARCHITECTURE.md) |
| module ownership | [Module Ownership](#module-ownership) | [Module Ownership Map](MODULE_OWNERSHIP_MAP.md) |
| maintainer migration notes | [Read Next](#read-next) | [Maintainer Architecture Notes](MAINTAINER_ARCHITECTURE_NOTES.md) |

Most users can stop after the first three sections.

## Architecture In One Page

SEOCHO has one public shape: a Python SDK plus an optional runtime service.
Internally, it is split into planes so graph behavior stays reviewable.

| Plane | Owns | Main paths |
|---|---|---|
| Public facade | stable user calls such as `Seocho.add()`, `Seocho.ask()`, `Seocho.local()`, `Seocho.remote()` | `src/seocho/client.py`, `src/seocho/session.py` |
| Ontology plane | schema contract, context hash, JSON-LD, offline governance | `src/seocho/ontology*.py` |
| Indexing plane | document ingest, extraction, linking, rule assessment, graph writes | `src/seocho/index/`, `src/seocho/rules.py` |
| Query plane | intent, retrieval, Cypher validation, evidence, answer synthesis | `src/seocho/query/` |
| Runtime shell | HTTP routes, policy, readiness, workspace/database scope | `runtime/` |
| Compatibility shell | legacy imports and batch compatibility while migration continues | `extraction/` |

The invariant is simple:

```text
ontology
  -> indexing prompt
  -> graph write metadata
  -> query intent and evidence
  -> trace
  -> supported answer
```

If that ontology context is lost, the system may still answer, but the answer is
not auditable in the way SEOCHO promises.

## Core Loop

```text
Documents or records
    -> ontology-shaped extraction
    -> graph facts with provenance
    -> semantic query planning
    -> graph evidence
    -> grounded answer
```

The default path is not "run every agent." It is:

1. ingest data against one ontology contract
2. write graph facts with provenance
3. resolve a question through the semantic layer
4. return an answer with evidence and trace metadata

Parallel debate is an advanced mode for explicit multi-graph comparison.

## Runtime Boundary

The runtime shell exposes the same graph contract over HTTP. It owns deployment
concerns, not canonical engine logic.

| Runtime concern | Owner |
|---|---|
| route wiring and request validation | `runtime/agent_server.py` |
| shared service composition | `runtime/server_runtime.py` |
| memory/search facade | `runtime/memory_service.py` |
| raw runtime ingestion | `runtime/runtime_ingest.py` |
| request policy | `runtime/policy.py` |
| readiness and graph-agent state | `runtime/agent_readiness.py` |
| request ID propagation | `runtime/middleware.py` |

`extraction-service` still exists as the local compose service name, but the
long-term package owner is `runtime/`. Compatibility aliases in `extraction/*`
keep older imports working while canonical runtime code moves into `runtime/`.

The supported local stack is intentionally small:

- `neo4j`
- `extraction-service`
- `evaluation-interface`

Use `docs/RUNTIME_ARCHITECTURE.md` when changing runtime internals. Use
`docs/RUNTIME_DEPLOYMENT.md` when you only need to run the service.

## Query Boundary

The query plane turns a user question into graph-grounded evidence and a
supported answer.

| Query step | Owner |
|---|---|
| intent and answerability | `src/seocho/query/intent.py` and related contracts |
| strategy selection | `src/seocho/query/strategy_chooser.py` |
| Cypher validation | `src/seocho/query/cypher_validator.py` |
| constraints and run metadata | `src/seocho/query/constraints.py`, `src/seocho/query/run_registry.py` |
| semantic specialists and routing | `src/seocho/query/semantic_agents.py` |
| shared orchestration | `src/seocho/query/semantic_flow.py` |

Rule of thumb: add query behavior under `src/seocho/query/`. Keep
`extraction/*` focused on compatibility, transport, and runtime injection.

For details, read `docs/QUERY_ARCHITECTURE.md`.

## Data And Evidence

SEOCHO keeps generated artifacts visible so users and maintainers can inspect
the system instead of treating it as a black box.

| Artifact | Usual location | Why it matters |
|---|---|---|
| ontology contract | `schema.jsonld` or a Python `Ontology` | defines the graph contract |
| graph state | `data/neo4j/` for the local compose stack | shows persisted graph facts |
| semantic artifacts | `outputs/semantic_artifacts/` | stores approved/draft semantic context |
| rule profile registry | `outputs/rule_profiles/rule_profiles.db` | records validation and promotion state |
| semantic run metadata | `outputs/semantic_metadata/` | explains query behavior after the run |
| traces | `SEOCHO_TRACE_JSONL_PATH` | preserves execution evidence |

Use `docs/FILES_AND_ARTIFACTS.md` for concrete inspection commands.

## Module Ownership

Use this table before editing code.

| Change area | Start in | Also check |
|---|---|---|
| public SDK calls | `src/seocho/client.py`, `src/seocho/session.py` | README and Python SDK docs |
| ontology behavior | `src/seocho/ontology*.py` | ontology docs and governance rules |
| indexing or graph shaping | `src/seocho/index/`, `src/seocho/rules.py` | examples and ingestion tests |
| query, retrieval, answering | `src/seocho/query/` | `docs/QUERY_ARCHITECTURE.md` and Graph-RAG contracts |
| runtime routes or policy | `runtime/` | runtime compatibility tests |
| legacy compatibility | `extraction/` | migration notes and alias tests |
| website/docs publishing | `docs/`, `website/`, `tteon.github.io` mirror | docs sync contract |

If a change crosses more than one row, explain the ownership boundary in the PR.

## Evidence Standard

Mocks are useful for contracts and no-service CI. They are not evidence for
throughput, latency, scalability, production readiness, or external-system
compatibility.

Performance or production claims need a live run against every named service.
The report should include:

- service versions
- dataset
- concurrency
- hardware or container limits
- warmup
- skipped components
- exact commands

If a live gate is unavailable, report the gap. Do not replace it with a mock
number.

## Read Next

| Need | Document |
|---|---|
| run the local service | [Runtime Deployment](RUNTIME_DEPLOYMENT.md) |
| understand runtime internals | [Runtime Architecture](RUNTIME_ARCHITECTURE.md) |
| understand query and Graph-RAG internals | [Query Architecture](QUERY_ARCHITECTURE.md) |
| find generated files | [Files and Artifacts](FILES_AND_ARTIFACTS.md) |
| contribute safely | [Open Source Playbook](OPEN_SOURCE_PLAYBOOK.md) |
| inspect internal migration notes | [Maintainer Architecture Notes](MAINTAINER_ARCHITECTURE_NOTES.md) |
| see detailed ownership | [Module Ownership Map](MODULE_OWNERSHIP_MAP.md) |
