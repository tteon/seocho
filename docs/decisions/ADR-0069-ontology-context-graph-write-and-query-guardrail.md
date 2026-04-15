# ADR-0069: Ontology Context Graph Write And Query Guardrail

Date: 2026-04-15
Status: Accepted

## Context

ADR-0068 introduced a compact ontology context descriptor and cache so SDK
indexing, querying, and agent sessions could refer to the same ontology
contract. That descriptor was visible in operation metadata, but graph data
itself still did not carry enough information to tell which ontology context
created a node or relationship.

That gap matters when users evolve an ontology profile over time. Without
stored context metadata, a query can run with the current ontology while reading
graph data indexed under an older contract and still appear normal.

## Decision

Persist compact ontology context metadata on local SDK graph writes.

The write payload receives `_ontology_*` properties on nodes and relationships,
including:

- `_ontology_context_hash`
- `_ontology_artifact_hash`
- `_ontology_glossary_hash`
- `_ontology_id`
- `_ontology_name`
- `_ontology_version`
- `_ontology_profile`
- `_ontology_graph_model`

Local query paths inspect indexed `_ontology_context_hash` values in the target
workspace and compare them with the active ontology context hash. Mismatches are
surfaced as `ontology_context_mismatch` metadata in local query traces and agent
query tool output.

The guardrail is intentionally non-blocking. A mismatch should warn operators
that re-indexing or profile selection may be needed, but it should not silently
change answer behavior or reject reads in this slice.

## Non-Decision

Do not add Rust, Arrow, GraphAr, Vineyard, DataBook, or a durable context
registry in this slice.

Do not redesign semantic evidence selection, debate routing, or Neo4j batch
write strategy here. Those remain separate work items because this slice only
establishes graph-level context provenance and query-time drift visibility.

## Consequences

### Positive

- graph nodes and relationships become auditable by ontology context
- SDK direct indexing and indexing agents share the same graph metadata helper
- local query and query-agent paths can surface ontology/profile drift
- future benchmarks can measure both answer quality and ontology-contract
  consistency

### Negative

- each local query performs a small metadata inspection query
- old graphs without `_ontology_*` properties can only report missing context,
  not prove a match
- HTTP runtime response contracts still need a later slice if public API clients
  should receive the same mismatch metadata

## Follow-up

- expose mismatch metadata through runtime HTTP semantic/memory responses
- cache context-inspection results if benchmark p95 shows measurable overhead
- use stored context hashes in benchmark reports and graph debugging tools
