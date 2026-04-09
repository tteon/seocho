# ADR-0026: Enterprise Vocabulary Layer with Global Access and Workspace Override

- Date: 2026-03-02
- Status: Accepted

## Context

Graph retrieval quality is highly sensitive to keyword variance (alias, hyphenation,
term drift). The existing semantic flow already uses fulltext resolution and ontology
hints, but it lacked a governed runtime vocabulary layer sourced from semantic
artifacts.

The platform also needed an explicit contract for:

- global vocabulary reuse across workspaces
- workspace-level override precedence
- lightweight runtime resolution without introducing heavy ontology reasoning in
  request hot paths

## Decision

1. Introduce a managed vocabulary resolver in semantic query flow.
2. Build runtime vocabulary from approved semantic artifacts, using:
   - global approved artifacts as baseline
   - workspace approved artifacts as overrides
3. Keep runtime behavior lookup-only (alias normalization + expansion hints), and
   keep heavy ontology reasoning offline (`owlready2` governance path).
4. Extend semantic artifact lifecycle/state handling to include deprecation:
   - `draft -> approved -> deprecated`
5. Include vocabulary candidate payload in semantic artifact and runtime ingest
   outputs for governance visibility.

## Consequences

Positive:

- improved resilience to keyword variation in query-time entity resolution
- explicit enterprise/global term reuse with workspace-specific control
- clear governance lifecycle for term promotion and retirement

Tradeoffs:

- additional lifecycle/API/storage surface area for semantic artifacts
- runtime alias resolution now depends on approved artifact integrity

## Implementation Notes

Key files:

- `extraction/semantic_vocabulary.py`
- `extraction/semantic_query_flow.py`
- `extraction/semantic_artifact_store.py`
- `extraction/semantic_artifact_api.py`
- `extraction/runtime_ingest.py`
- `extraction/agent_server.py`
- tests under `extraction/tests/`
