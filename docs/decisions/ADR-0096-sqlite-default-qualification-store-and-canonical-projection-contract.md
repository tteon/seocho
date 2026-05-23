Date: 2026-05-23
Status: Accepted

# ADR-0096: SQLite-Default Qualification Store And Canonical Projection Contract

## Context

SEOCHO local ingest now materializes a layered memory graph:

- `Document`
- `DocumentVersion`
- `Section`
- `Chunk`
- `Entity`

That is a good substrate for retrieval and provenance, but it is not a safe
place to run identity curation directly. Entity merge decisions need to preserve
observed source instances, conflicting properties, and reviewer actions without
destructively rewriting the raw ingest graph.

The existing local pipeline still performs light graph-write dedup for practical
reasons. A qualification workflow therefore needs a separate observed-data
record that survives later merge/projection decisions.

## Decision

Introduce a local-SDK-first qualification slice with three contracts:

1. `CurationDesignSpec`
   - user-owned policy contract for identity keys, fallback keys, property
     merge strategies, and promotion thresholds
2. `QualificationStore`
   - SQLite-default embedded tabular store for observed entities, observed
     relations, chunks, qualification runs, curation cases, decisions, and
     projection artifacts
   - DuckDB remains an optional backend for larger offline analytical passes
     against the same contract
3. `GraphProjector`
   - builds canonical entity / relation snapshots from the qualification store
     and writes them into the graph store as a serving projection

Public local SDK surfaces:

- `Seocho.qualify_graph(...)`
- `Seocho.list_curation_cases(...)`
- `Seocho.preview_curation_decision(...)`
- `Seocho.apply_curation_decision(...)`
- `Seocho.project_canonical_graph(...)`

## Rules

- observed ingest is recorded before cross-chunk dedup mutates node state
- ontology-sensitive identity keys are treated as hard merge guards
- curation decisions do not rewrite the raw observed ingest rows
- canonical relations are grouped by:
  - relation type
  - canonical source endpoint
  - canonical target endpoint
  - qualifier hash

This means same-type relations with different qualifiers stay distinct canonical
relation instances.

## Consequences

Benefits:

- preserves provenance and conflicting observed values
- lets users review merge decisions separately from ingest
- keeps Graph-RAG answer substrate pointed at a curated canonical projection
- keeps local install friction low because the default mutable store is stdlib
  SQLite

Tradeoffs:

- introduces a second local persistence surface
- first slice is local-SDK-only; runtime HTTP endpoints are deferred
- current local graph write path still performs lightweight dedup, so the
  qualification store is the authoritative observed-record surface for curation
- DuckDB-based analytics remain opt-in instead of baseline

## Follow-ups

- add runtime endpoint parity for qualification and projection
- extend curation cases from entity identity to explicit relation-instance
  review lanes where human approval is needed
- surface qualification traces alongside semantic answer traces
