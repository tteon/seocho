# ADR-0093: Layered Document-Version-Chunk Ingest Contract

Date: 2026-05-23
Status: Accepted

## Context

SEOCHO already had ontology-first extraction plus a `Document`-centric memory
graph, but the local SDK ingest path still collapsed too much provenance:

- chunk embeddings were optional and not wired into the local ingest pipeline
- the graph lacked an explicit immutable ingest snapshot layer
- chunk-level vector hits did not have a canonical join contract back to the
  graph structure

That made it harder to support the practical pattern we want for Graph-RAG:

`chunk retrieval -> graph expansion -> evidence bundle -> grounded answer`

## Decision

Adopt a layered ingest contract for the local SDK path:

- `Document` remains the logical memory/source anchor
- `DocumentVersion` becomes the immutable ingest snapshot for one indexing run
- `Chunk` becomes the vector retrieval unit and provenance bridge
- `Entity` remains the graph reasoning and cross-document join unit

When a local SDK client is constructed with a `vector_store`, the indexing
pipeline writes chunk rows into that vector backend after a successful graph
write. Each row must preserve:

- `workspace_id`
- `memory_id` / `source_id`
- `document_id`
- `version_id`
- `chunk_id`
- `ordinal`

The graph write now preserves matching layered nodes/edges:

- `(Document)-[:HAS_VERSION]->(DocumentVersion)`
- `(Document)-[:CURRENT_VERSION]->(DocumentVersion)`
- `(DocumentVersion)-[:HAS_CHUNK]->(Chunk)`
- `(Chunk)-[:NEXT]->(Chunk)`
- `(Chunk)-[:MENTIONS]->(Entity)`

The existing `Document -> Entity` `MENTIONS` edges remain for compatibility.

## Consequences

Positive:

- vector retrieval is now joinable back to graph provenance with stable ids
- the local SDK has an explicit ingest snapshot layer instead of overwriting all
  semantics into one `Document` node
- chunk-first retrieval and graph-grounded answering now share a concrete local
  contract

Tradeoffs:

- ingest writes more nodes and relationships per memory
- write counts in local indexing tests and smoke fixtures increase
- vector indexing is now part of the local ingest success path when a
  `vector_store` is supplied

## Implementation Notes

- graph shaping: `seocho/index/runtime_memory.py`
- indexing orchestration: `seocho/index/pipeline.py`
- local SDK wiring: `seocho/local_engine.py`, `seocho/client.py`,
  `seocho/session.py`
- tests: `tests/seocho/test_runtime_helpers.py`,
  `tests/seocho/test_indexing.py`
