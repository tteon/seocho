# ADR-0048: Canonical Query Engine First Slice

## Status

Accepted

## Context

SEOCHO query behavior exists in both the local SDK runtime and the server
runtime, but the responsibilities are spread across multiple modules:

- local SDK:
  - `seocho/client.py`
  - `seocho/query/cypher_builder.py`
  - `seocho/query/strategy.py`
- server/runtime:
  - `extraction/semantic_query_flow.py`
  - `extraction/memory_service.py`

This makes query harder to reason about than indexing, which already has a more
visible canonical engine surface under `seocho/index`.

## Decision

Introduce a first canonical query engine surface under `seocho/query/`:

- `seocho/query/contracts.py`
- `seocho/query/planner.py`
- `seocho/query/executor.py`
- `seocho/query/answering.py`

The first slice does not redesign retrieval policy. It extracts shared planner,
executor, and answering responsibilities into canonical modules and makes both
local SDK and server/runtime import these shared contracts for at least one core
query path.

## Consequences

Positive:

- query has a clearer canonical home under `seocho/`
- local SDK query planning/execution/answer synthesis are less concentrated in
  `seocho/client.py`
- server semantic evidence-bundle shaping reuses shared query answering
  utilities instead of owning a parallel copy

Tradeoffs:

- this is only the first slice; server query orchestration still lives in
  `extraction/semantic_query_flow.py`
- local and server query parity is improved, but not yet complete

## Follow-up

- continue with canonical agent engine work
- split ontology into clearer canonical subdomains
- move more server query orchestration behind the `seocho/query` engine
