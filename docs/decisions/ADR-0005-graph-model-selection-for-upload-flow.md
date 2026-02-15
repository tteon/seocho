# ADR-0005: Graph Model Selection for Upload Flow

- Status: Accepted
- Date: 2026-02-15
- Deciders: SEOCHO team

## Context

Frontend upload flow needs a graph representation strategy that:

- minimizes hardcoded domain assumptions,
- keeps ontology governance manageable,
- supports both local and global retrieval patterns.

## Decision

Adopt a layered representation strategy:

1. Document structure layer (`Document`/`Section`/`Chunk`)
2. Entity interaction layer (`Entity`, extracted relations)
3. Optional community layer (`Community`) for global synthesis

Ontology governance boundary:

- Owlready2 in offline control-plane workflow
- runtime request path remains lightweight and DB/query focused

## Why

- aligns with GraphRAG model families without overcommitting to one shape early
- preserves provenance while enabling entity- and summary-level retrieval
- reduces runtime latency risk from heavy ontology reasoning

## References

- `docs/GRAPH_MODEL_STRATEGY.md`
