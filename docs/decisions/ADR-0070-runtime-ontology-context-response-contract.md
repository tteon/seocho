# ADR-0070: Runtime Ontology Context Response Contract

Date: 2026-04-15
Status: Accepted

## Context

ADR-0069 made local SDK graph writes and local query traces ontology-context
aware. Runtime HTTP users still had to infer context drift indirectly from raw
graph data or logs.

That is weak library UX. Users calling `chat(...)`, `search_with_context(...)`,
or `semantic(...)` should be able to inspect whether the runtime graph appears
aligned with the active graph target without dropping below the public SDK.

## Decision

Expose `ontology_context_mismatch` in runtime HTTP memory search/chat and
semantic query responses, and parse it into the typed Python SDK response
objects.

Runtime responses now include database-level provenance status:

- expected graph target ontology ID
- expected graph target vocabulary profile
- indexed ontology IDs
- indexed ontology context hashes
- indexed profiles
- missing context metadata counts
- mismatch reasons and warning text

Runtime ingest also attaches compact `_ontology_id`, `_ontology_profile`, and
graph-model metadata to graph payloads before loading. It does not synthesize a
fake SDK `ontology_context_hash` because the runtime graph registry is not the
same thing as a compiled local SDK ontology descriptor.

## Non-Decision

Do not block answers on mismatch. This slice is an audit and user-interface
contract, not an authorization or correctness gate.

Do not add a durable ontology context registry, DataBook bundle, Arrow/GraphAr
format, or Rust extension in this slice.

## Consequences

### Positive

- HTTP SDK users can inspect context drift directly from typed responses
- runtime-created graph data carries enough ontology metadata for future audits
- semantic and memory responses expose the same high-level guardrail shape
- no hot-path ontology reasoning or new infrastructure dependency is required

### Negative

- runtime query responses perform a small graph metadata inspection
- legacy graphs may show incomplete context metadata until re-indexed
- runtime context status is registry-based and cannot prove full SDK descriptor
  hash parity unless graph data was indexed by the local SDK path

## Follow-up

- measure the metadata inspection overhead in benchmark runs
- consider caching database-level context status if p95 latency shows impact
- decide whether graph registry entries should eventually carry approved
  ontology context hashes
