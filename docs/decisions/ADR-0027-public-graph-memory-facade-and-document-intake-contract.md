# ADR-0027 Public Graph Memory Facade And Document Intake Contract

Date: 2026-03-12
Status: Accepted

## Context

SEOCHO is moving toward a memory-first public product surface inspired by mem0's graph-memory experience, while keeping SEOCHO's ontology, vocabulary, provenance, and graph reasoning strengths.

At the same time, the repository is being optimized for agent-friendly development. That requires document conventions that coding agents can interpret mechanically instead of inferring intent from free-form prose.

## Decision

We will:

1. expose a public graph-memory facade based on stable resource-oriented APIs
2. keep orchestration-heavy routes as internal or expert surfaces
3. treat ingested runtime memories as provenance-backed graph resources anchored by `Document` nodes
4. standardize SKOS-compatible vocabulary artifacts as the runtime-facing vocabulary contract
5. adopt explicit `DEV-*` document prefixes as the implementation intake contract for coding agents

## Public Surface

The intended stable public routes are:

- `POST /api/memories`
- `POST /api/memories/batch`
- `GET /api/memories/{memory_id}`
- `POST /api/memories/search`
- `DELETE /api/memories/{memory_id}`
- `POST /api/chat`

These routes are memory-first and hide internal orchestration details by default.

## Internal Surface

The following remain implementation or expert routes:

- `/platform/ingest/raw`
- `/platform/chat/send`
- `/run_agent`
- `/run_agent_semantic`
- `/run_debate`
- `/rules/*`
- `/semantic/artifacts/*`
- `/indexes/fulltext/ensure`

## Data Contract

Each public memory ingest should:

- preserve `workspace_id`
- create or maintain a `Document` provenance node
- keep `memory_id` aligned with runtime `source_id`
- attach `content`, `content_preview`, and serialized metadata on the provenance node
- attach extracted graph nodes and relationships with the same `memory_id`
- allow archive behavior by setting memory status instead of hard-deleting by default

Vocabulary candidates are standardized as:

- `schema_version = vocabulary.v2`
- `profile = skos`
- `terms[*].pref_label`
- `terms[*].alt_labels`
- `terms[*].hidden_labels`
- optional `broader`, `related`, `definition`, and `examples`

## Agent Intake Contract

Product and engineering docs that drive implementation should use explicit prefixes such as:

- `DEV-INPUT-REQUIRED`
- `DEV-DECISION`
- `DEV-CONSTRAINT`
- `DEV-ASSUMPTION`
- `DEV-OUT-OF-SCOPE`
- `DEV-ACCEPTANCE`
- `DEV-API-CONTRACT`
- `DEV-DATA-CONTRACT`
- `DEV-TEST-REQUIRED`
- `DEV-FOLLOW-UP`

These markers are the repository's document-first control surface for agent execution.

## Consequences

Positive:

- clearer separation between stable public API and internal runtime routes
- easier agent execution because missing decisions are explicitly marked
- better provenance and archive behavior for graph-memory use cases
- stronger alignment between ontology/vocabulary governance and runtime retrieval

Tradeoffs:

- more contracts to maintain across docs, tests, and runtime code
- public API migration work must be carried in parallel with legacy routes
- `agent_server.py` remains transitional until more router extraction is complete
