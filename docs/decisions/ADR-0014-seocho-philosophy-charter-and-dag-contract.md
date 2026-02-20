# ADR-0014: SEOCHO Philosophy Charter and DAG Trace Contract

- Date: 2026-02-20
- Status: Accepted

## Context

SEOCHO has evolved into a platform that must integrate:

- heterogeneous data extraction and semantic governance,
- ontology-guided entity linking and graph conversion,
- per-graph agent orchestration and router/supervisor allocation,
- production-grade observability and frontend DAG visualization.

Without an explicit charter, implementation can drift toward partial optimizations that break architectural intent.

## Decision

Adopt an explicit philosophy charter (`docs/PHILOSOPHY.md`) and make it an operating contract.

Key commitments:

1. SHACL-like semantic extraction from heterogeneous data is a first-class path.
2. Table-first persistence and ontology artifacts (`.ttl` and related files) are governance evidence.
3. Ontology-aware prompting is required for extraction/linking quality.
4. Graph instance to graph-agent mapping remains 1:1 by default.
5. Router agent remains default query entrypoint and delegates by graph capability.
6. Router/graph-agent interactions follow supervisor-style orchestration with ontology metadata.
7. Opik is mandatory for agent-layer flow tracking.
8. Backend trace topology metadata is a strict contract for frontend DAG rendering.

## Consequences

Positive:

- architecture intent is explicit and testable
- backend/frontend contract is stabilized for DAG canvas UX
- governance and observability become auditable product capabilities

Tradeoffs:

- stronger process requirements for doc/ADR updates
- additional implementation overhead to maintain topology and trace contracts

## Implementation Notes

- reference doc: `docs/PHILOSOPHY.md`
- workflow intake now includes philosophy alignment review (`docs/WORKFLOW.md`)
- `CLAUDE.md` includes philosophy alignment checks for agent implementation
