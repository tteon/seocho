# ADR-0068: Ontology Context Cache And Agent Middleware Seam

Date: 2026-04-15
Status: Accepted

## Context

SEOCHO's durable advantage should come from shared ontology contracts that stay
consistent across indexing, querying, and agent interaction. The risky version
of this idea would add a new storage format, a Rust service, or hot-path RDF
reasoning before the Python SDK contract is stable.

The current product already has JSON-LD ontology persistence, semantic artifact
promotion, SHACL-like validation, deterministic query planning, and agent
sessions. The missing seam is a compact runtime identity for the ontology
context used by each operation.

## Decision

Introduce `seocho/ontology_context.py` as the canonical first slice for shared
ontology context middleware.

The module provides:

- `OntologyContextDescriptor`
- `CompiledOntologyContext`
- `OntologyContextCache`
- `compile_ontology_context(...)`

The descriptor records a stable `context_hash`, `artifact_hash`,
`glossary_hash`, `ontology_id`, version, profile, graph model, labels,
relationship types, glossary term count, and deterministic query intents. The
compiled context keeps larger extraction,
query, and agent prompt artifacts in-process instead of storing them in every
trace or metadata payload.

Indexing metadata, query traces, and agent session context should carry the
compact descriptor so operators can verify that write-path and read-path
behavior used the same ontology contract.

## Non-Decision

Do not add Rust, Arrow, GraphAr, Vineyard, or a DataBook dependency in this
slice.

Those options may be useful later for portable artifact exchange, high-volume
columnar workloads, or self-hosted model/runtime optimization. They are not the
right first step for middleware optimization because the current bottleneck is
contract drift and context identity, not CPU-bound ontology serialization.

## Consequences

### Positive

- indexing, query, and agent sessions gain a shared ontology context identity
- SKOS-style glossary aliases can invalidate the context identity without
  making normalization/denormalization depend on a new service
- the implementation stays dependency-free and hot-path safe
- future benchmarks can verify both latency and ontology-contract consistency
- a future DataBook-like bundle can wrap this descriptor without changing SDK
  runtime behavior

### Negative

- this does not yet provide a durable cross-process ontology context registry
- HTTP runtime paths still need a later slice to expose the same descriptor in
  public API responses
- cache invalidation assumes ontologies are mostly immutable after construction

## Follow-up

- expose the descriptor through runtime HTTP memory/search responses
- add optional local artifact bundle export if users need portable context
  packages
- measure cache hit rate and latency effects during local benchmark runs
