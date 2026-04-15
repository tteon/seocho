# ADR-0073: PropertyGraphLens Semantic Overlay

Date: 2026-04-15
Status: Accepted

## Context

SEOCHO uses DozerDB/Neo4j as a property graph backend. A property graph's
advantage is that nodes and relationships can carry flexible labels and
properties. If SEOCHO forces every graph element into a rigid ontology shape, it
weakens that advantage and creates unnecessary normalization work.

At the same time, agents need stable guidance. They should not inspect raw graph
dumps or treat every node/edge as equally trustworthy. They need a small,
deterministic view of important anchors, evidence paths, confidence, source
provenance, and ontology context metadata.

## Decision

Adopt `PropertyGraphLens` as the target semantic overlay strategy.

The raw graph remains schemaless and heterogeneous. SEOCHO only marks special
nodes and relationships with optional `_seocho_*`, `_agent_visible`,
`_importance`, `_confidence`, provenance, workspace, and ontology context
metadata.

Agents should consume bounded lens views instead of raw graph dumps:

- schema cards
- property profiles
- visible neighborhood probes
- evidence path bundles

## Consequences

- SEOCHO keeps the practical benefit of schemaless property graphs.
- Ontology becomes an agent-readable overlay, not a mandatory total schema.
- Query and debate paths can prefer high-confidence visible anchors and
  evidence paths without discarding unannotated graph data.
- Missing overlay metadata is not invalid data; it is a retrieval confidence
  signal.
- The first implementation should stay read-only and bounded before adding
  ranking, analytics, or native acceleration.

## Implementation Order

1. Add canonical overlay constants and pure helpers.
2. Annotate only high-confidence anchors, evidence sources, and evidence edges
   during indexing.
3. Add bounded read-only lens helpers for schema cards, property profiles, and
   neighborhoods.
4. Feed selected lens paths into evidence bundles and missing-slot reporting.
5. Surface per-agent lens summaries in debate.

## Non-Goals

- Do not require every graph element to carry ontology metadata.
- Do not expose full graph dumps to agents.
- Do not run expensive graph analytics in the request hot path.
- Do not introduce Rust, graph embeddings, Arrow, GraphAr, DataBook, or vineyard
  before measuring the Python/DozerDB baseline.

## Related Documents

- `docs/PROPERTY_GRAPH_LENS_STRATEGY.md`
- `docs/ONTOLOGY_RUN_CONTEXT_STRATEGY.md`
- `docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md`
- `docs/decisions/ADR-0072-ontology-run-context-strategy.md`
