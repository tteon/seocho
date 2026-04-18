# OntologyRunContext Strategy

Date: 2026-04-15
Status: Active design plan

## Purpose

SEOCHO should treat ontology metadata as the middleware contract between the
library user interface, agents, tools, and graph databases.

The goal is not to make ontology reasoning heavy or magical. The goal is to
make every indexing and query path carry enough compact context to answer these
questions:

- Which workspace, graph, database, ontology, and vocabulary profile governed
  this operation?
- Did the agent/tool touch only the databases it was allowed to touch?
- Was the indexed graph produced under the same ontology context as the query?
- Which schema terms, glossary aliases, and intent slots shaped retrieval?
- If the answer is weak, is the miss caused by scope, entity resolution,
  missing relationships, missing slots, or ontology drift?

## Core Contract

Introduce a canonical `OntologyRunContext` concept before adding more agent
behavior. The first implementation can be a typed Python dataclass or Pydantic
model; the important part is the contract, not the storage format.

Required fields:

- `workspace_id`: tenant/workspace scope.
- `user_id`, `agent_id`, `session_id`: caller and conversation scope when
  present.
- `turn_id`: per-turn identifier for multi-turn traceability.
- `graph_ids`: public graph routing identifiers requested by the user or
  selected by policy.
- `databases`: resolved DozerDB/Neo4j database names used by tools.
- `ontology_id`: active ontology package or graph target ontology identifier.
- `ontology_profile`: active profile for extraction/query behavior.
- `vocabulary_profile`: active glossary/vocabulary profile.
- `ontology_context_hash`: compact identity of ontology + profile + glossary.
- `glossary_hash`: compact identity of labels, aliases, and hidden labels.
- `reasoning_mode`, `repair_budget`: query repair settings.
- `tool_budget`: maximum tool calls allowed for the turn.
- `allowed_databases`: database scope enforced by tool middleware.
- `policy_decision`: allow/warn/block decision plus reason.
- `ontology_context_mismatch`: graph-indexed context parity metadata.
- `property_graph_lens`: compact schema/property/overlay summary for visible
  anchors, evidence paths, and bounded neighborhood probes when a graph-native
  query path needs it.
- `evidence_state`: intent, slot fill, missing slots, and selected triples
  when the query path emits evidence.

## Lifecycle

1. Resolve request scope.
   Convert `graph_ids`, `databases`, session defaults, and workspace policy into
   a single graph/database scope.

2. Resolve ontology context.
   Compile or fetch the active ontology context descriptor. Runtime graph
   targets may only provide `ontology_id` and `vocabulary_profile`; local SDK
   mode can provide full `ontology_context_hash`.

3. Attach agent/session context.
   The context should be available to router, semantic, debate, and platform
   chat paths before any tool call.

4. Enforce tool preflight.
   Tool middleware checks workspace, allowed database scope, tool budget, and
   write/read policy before executing Cypher or ingest actions.

5. Query/write graph.
   Ingest writes compact `_ontology_*` metadata. Query paths inspect indexed
   context metadata and return non-blocking mismatch status. Provenance checks
   should prefer document-scoped metadata queries over broad all-node scans so
   mixed-property graphs do not flood runtime logs with missing-property
   warnings.

6. Emit response metadata.
   Every public response that can touch graph state should expose
   `ontology_context_mismatch` at the top level. Evidence-capable responses
   should additionally expose intent/slot status.

## Scenario Matrix

| Scenario | Required behavior | Ontology role |
|---|---|---|
| Single-turn direct query | Resolve graph/database scope for the request and answer once | Provide schema, glossary aliases, context hash, and mismatch metadata |
| Multi-turn session | Carry previous graph/entity/context defaults, detect profile drift across turns | Warn when the session changes ontology context or graph scope mid-conversation |
| Reasoning disabled | Use ontology mostly as prompt/schema context and metadata | Keep fast path lightweight and deterministic |
| Reasoning enabled | Use ontology intent slots and allowed relations during repair | Rank or abstain by slot fill, not only text relevance |
| Tool use | Preflight every tool call against allowed databases and policy | Prevent graph scope leakage and surface context mismatch beside tool results |
| Debate | Each graph agent runs with its own context; supervisor sees per-agent status | Compare answers with ontology/profile provenance instead of treating graphs as interchangeable |
| Policy/authz | Workspace and database scope violations block; ontology mismatch warns by default | Keep security hard, ontology parity advisory unless explicitly configured stricter |
| Ingest/indexing | Persist compact `_ontology_*` metadata on graph payloads | Make later query-time parity auditable without hot-path reasoning |

## Alignment Rules

- Scope alignment:
  `graph_ids` must resolve to concrete databases before agent execution. Tools
  should read `allowed_databases`, not raw user input.

- Schema alignment:
  Query planners and repair loops should prefer labels, relationships, and
  properties from the active ontology context.

- Glossary alignment:
  Normalization should use ontology labels, aliases, hidden labels, and
  vocabulary terms. A glossary change should alter `glossary_hash` and therefore
  the context identity.

- Evidence alignment:
  Graph-RAG answers should move from `question -> candidates` to
  `intent -> required slots -> selected triples -> answer`.

- Policy alignment:
  Authz, workspace, and database access are blocking policy checks. Ontology
  mismatch is a warning unless an explicit strict mode is enabled.

- Observability alignment:
  Trace metadata should include context hash, graph/database scope, policy
  decision, mismatch summary, and evidence slot status.

## Implementation Plan

### Stage 0. Current Baseline

Already landed or in progress:

- local indexing attaches compact ontology metadata to graph writes
- local query and runtime memory/semantic paths expose mismatch metadata
- router/debate/platform/plan responses expose top-level
  `ontology_context_mismatch`
- router `graph_ids` now bind database scope for tool middleware
- `PropertyGraphLens` is the target semantic overlay strategy for preserving
  schemaless property graph flexibility while marking only agent-visible graph
  elements

### Stage 1. Canonical Context Model

Add `seocho/ontology_run_context.py` with:

- `OntologyRunContext`
- `OntologyPolicyDecision`
- `OntologyEvidenceState`
- optional `PropertyGraphLens` summary fields
- helpers to build context from local SDK, runtime graph targets, and session
  state

This is a typed contract only; do not add new storage or Rust.

### Stage 2. Tool Middleware Integration

Update graph tools to receive the context and enforce:

- workspace scope
- allowed database scope
- read/write action type
- tool budget
- mismatch warning propagation

### Stage 3. Session Carryover

Persist the last context summary in `SessionContext` and platform chat history:

- previous graph scope
- previous ontology/profile/hash
- entity overrides
- mismatch warning

Multi-turn changes should be visible, not silently blended.

### Stage 4. Debate Context Summary

Debate responses should include per-agent context summaries:

- graph ID
- database
- ontology ID/profile
- mismatch status
- support/evidence status when available

The supervisor should prefer grounded, non-drifted evidence when answers
conflict, but should not hide useful partial evidence.

### Stage 5. Evidence And Evaluation

Extend evidence-bundle work to record:

- intent ID
- required relations and entity types
- filled/missing slots
- selected triples
- abstention reason

Use this before adding GraphRAG-Bench or private finance corpus benchmark comparisons so the
failure modes are inspectable.

## Non-Goals

- Do not add Rust for this middleware layer until Python p95/tool overhead is
  measured.
- Do not add Arrow, GraphAr, DataBook, or vineyard to the hot path yet.
- Do not run Owlready2 reasoning inside request-time agent paths.
- Do not make ontology mismatch block by default; teams can add strict mode
  later.

## Practical User Interface Target

The library user experience should stay simple:

```python
result = (
    client.plan("Compare ACME revenue risks")
    .on_graph("finance_kg")
    .with_repair_budget(2)
    .run()
)

print(result.response)
print(result.ontology_context_mismatch.get("warning", ""))
print(result.evidence.missing_slots if hasattr(result, "evidence") else [])
```

The user asks a graph question. SEOCHO carries ontology context, policy status,
tool scope, and evidence health beside the answer.
