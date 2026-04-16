# Developer Input Conventions

Date: 2026-03-12
Status: Draft

This document defines how product and engineering inputs should be written in `docs/` so coding agents can consume them without guessing.

The goal is simple:

- make missing decisions explicit
- make assumptions reviewable
- let developers fill in required context before implementation starts

## 1. Required Prefixes

Use these prefixes at the start of a line or subsection heading.

### `DEV-INPUT-REQUIRED:`

Use when an agent must not guess.

Examples:

- `DEV-INPUT-REQUIRED: Canonical public memory delete behavior is archive-only, not hard delete.`
- `DEV-INPUT-REQUIRED: Dynamic ontology prompt must prioritize approved artifact vocabulary over draft vocabulary.`

### `DEV-DECISION:`

Use for a confirmed product or architecture decision.

Examples:

- `DEV-DECISION: SEOCHO borrows only a generic graph-memory interaction model, not any specific peer product surface.`
- `DEV-DECISION: Public memory APIs default to soft archive semantics.`

### `DEV-CONSTRAINT:`

Use for a hard technical or operational boundary.

Examples:

- `DEV-CONSTRAINT: workspace_id is mandatory on every runtime-facing API.`
- `DEV-CONSTRAINT: owlready2 stays in the offline ontology governance path only.`

### `DEV-ASSUMPTION:`

Use when the current draft allows implementation to proceed unless later corrected.

Examples:

- `DEV-ASSUMPTION: Public memory writes default to kgnormal until a dedicated memory database is introduced.`
- `DEV-ASSUMPTION: Memory status values are active and archived for MVP.`

### `DEV-OUT-OF-SCOPE:`

Use to block accidental feature expansion.

Examples:

- `DEV-OUT-OF-SCOPE: Full compatibility with any specific peer graph-memory product is not part of this MVP.`
- `DEV-OUT-OF-SCOPE: Online ontology reasoning is excluded from runtime request handling.`

### `DEV-ACCEPTANCE:`

Use for implementation acceptance criteria.

Examples:

- `DEV-ACCEPTANCE: A stored memory must be retrievable by memory_id with provenance and extracted entity summary.`
- `DEV-ACCEPTANCE: Search results must filter by workspace_id and exclude archived memories.`

### `DEV-API-CONTRACT:`

Use for stable request or response expectations.

Examples:

- `DEV-API-CONTRACT: POST /api/memories returns memory, ingest_summary, and trace_id.`
- `DEV-API-CONTRACT: Public responses expose memory_id, not graph node ids, by default.`

### `DEV-DATA-CONTRACT:`

Use for graph, ontology, vocabulary, or metadata shape decisions.

Examples:

- `DEV-DATA-CONTRACT: Runtime provenance uses Document nodes with memory_id = source_id.`
- `DEV-DATA-CONTRACT: Vocabulary artifacts are SKOS-compatible and use vocabulary.v2 with pref_label and alt_labels.`

### `DEV-TEST-REQUIRED:`

Use for mandatory verification when an area changes.

Examples:

- `DEV-TEST-REQUIRED: Runtime ingest changes must run focused pytest for runtime_ingest, semantic_query_flow, and API endpoint coverage.`
- `DEV-TEST-REQUIRED: Public API shape changes must add or update endpoint tests before landing.`

### `DEV-FOLLOW-UP:`

Use for explicitly deferred work.

Examples:

- `DEV-FOLLOW-UP: Add persistent trace lookup API after public memory facade is stable.`
- `DEV-FOLLOW-UP: Split agent_server.py into api/routes/services/runtime modules.`

## 2. Where To Use Prefixes

Use the prefixes in these files first:

1. `docs/PRD_MVP.md`
2. `docs/GRAPH_MEMORY_API.md`
3. `docs/AGENT_DEVELOPMENT.md`
4. `docs/CODING_STYLE.md`

Recommended usage:

- product behavior goes in `PRD_MVP.md`
- public API behavior goes in `GRAPH_MEMORY_API.md`
- implementation guardrails go in `AGENT_DEVELOPMENT.md`
- code and test conventions go in `CODING_STYLE.md`
- repo gaps or blocked decisions should become follow-up work items in `.beads`

## 3. Minimum Completion Standard Before Development

Before substantial implementation starts, the docs should contain at least:

- `DEV-DECISION` for product scope
- `DEV-CONSTRAINT` for runtime invariants
- `DEV-API-CONTRACT` for public memory endpoints
- `DEV-DATA-CONTRACT` for memory/provenance/ontology/vocabulary shape
- `DEV-ACCEPTANCE` for MVP behavior
- any unresolved blockers marked with `DEV-INPUT-REQUIRED`

If a required area is still unknown, mark it explicitly. Do not leave it implicit.

## 4. Agent Execution Rule

Agents should follow this rule:

1. read the standard repo docs in `AGENTS.md`
2. read the product and API docs
3. collect all lines marked `DEV-INPUT-REQUIRED`
4. if the missing inputs materially affect correctness, stop and request clarification
5. otherwise proceed only on documented `DEV-ASSUMPTION` lines

This keeps implementation tied to explicit developer intent.

## 5. Recommended Authoring Pattern

Use short blocks like this:

```md
DEV-DECISION: SEOCHO will expose a memory-first graph-memory facade.
DEV-CONSTRAINT: Public memory endpoints must preserve workspace_id and soft archive semantics.
DEV-DATA-CONTRACT: Every ingested memory creates a Document provenance node and extracted entity nodes with shared memory_id.
DEV-ACCEPTANCE: Searching a memory must return evidence tied to memory_id, not internal node ids.
DEV-FOLLOW-UP: Add trace lookup endpoint after public memory CRUD and search are stable.
```

## 6. Development Flow After Docs Are Filled

Once the developer fills the docs:

1. agent reads `README.md`, `CLAUDE.md`, `docs/WORKFLOW.md`, `docs/ISSUE_TASK_SYSTEM.md`, and `docs/decisions/DECISION_LOG.md`
2. agent reads the filled product/API/style docs
3. agent opens or picks a `.beads` task
4. agent restates the implementation contract from `DEV-*` lines
5. agent implements only within that contract
6. agent updates tests and docs
7. agent lands the change and records follow-up items for remaining `DEV-FOLLOW-UP` work

This is the intended document-first implementation loop for SEOCHO.
