# ADR-0094: Section Layer And Structured Local Ingest Contract

Date: 2026-05-23

## Status

Accepted

## Context

ADR-0093 established a layered local ingest contract around
`Document -> DocumentVersion -> Chunk -> Entity`, with vector rows keyed by
`chunk_id`, `document_id`, and `version_id`.

That slice improved provenance and vector joinability, but two gaps remained:

1. text ingest still dropped most document hierarchy between document and chunk,
   so parent-child retrieval and graph-grounded navigation had no explicit
   `Section` anchor
2. callers who already had ontology-shaped nodes and relationships had to
   either re-run text extraction through `add(...)` or jump to runtime
   `raw_ingest(...)`, with no local SDK surface that preserved the same
   ontology validation and memory-graph shaping contract

## Decision

SEOCHO local ingest now adopts two additional contracts.

### 1. Materialize a `Section` layer in local text ingest

- canonical local text ingest materializes
  `Document -> DocumentVersion -> Section -> Chunk -> Entity`
- chunk metadata carries `section_path`, `section_title`, and `section_level`
- section hierarchy is inferred heuristically from markdown-style headings in
  source text
- the graph materializer writes:
  - `HAS_SECTION` from `DocumentVersion` to top-level sections
  - `PART_OF` from child sections to parent sections
  - `HAS_CHUNK` from both `DocumentVersion` and leaf `Section` nodes to chunks

### 2. Add a local structured ingest surface

- local `Seocho.add_graph(...)` accepts caller-supplied ontology-shaped
  `nodes` / `relationships`
- the structured path reuses the canonical local indexing pipeline for:
  - ontology validation / strict validation behavior
  - ontology-context stamping
  - semantic artifact draft reporting
  - layered memory-graph shaping
  - vector-row metadata when chunk records or canonical content are available
- remote/runtime structured ingestion remains `raw_ingest(...)`; `add_graph(...)`
  is local-engine-only for this slice

## Consequences

### Positive

- graph-grounded retrieval gets an explicit structural anchor between document
  and chunk
- vector hits can expand through section context before broader document context
- ontology-first teams can load pre-structured graphs locally without losing
  validation or provenance contracts
- `IndexingDesignSpec` defaults apply consistently to both `add(...)` and
  `add_graph(...)`

### Trade-Offs

- text ingest writes more nodes and relationships per document
- section inference is heuristic and intentionally lightweight; it does not try
  to be a full document parser
- structured local ingest does not replace runtime `raw_ingest(...)` for batch
  ETL or API-driven loads

## Follow-Up

- consider richer section extraction for PDFs/HTML where heading structure is
  available outside markdown
- add a first-class `Claim` / `Observation` layer once slot-grounded graph QA
  becomes the next bottleneck
