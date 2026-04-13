# ADR-0051: Client Facade Boundary First Slice

- Status: Accepted
- Date: 2026-04-13

## Context

`seocho/client.py` had become the default place for:

- public SDK entrypoints
- HTTP transport and exception mapping
- ontology artifact bridge helpers
- local engine orchestration

That made `Seocho` convenient, but it also kept growing into a god-object and
made canonical query/agent/ontology modules harder to see.

## Decision

Keep `Seocho` as the stable public facade, but extract the clearest supporting
boundaries first:

- `seocho/http_transport.py`
  - HTTP request handling and error promotion
- `seocho/client_artifacts.py`
  - ontology-to-approved-artifact and prompt-context bridge helpers

`Seocho` now delegates to those helpers instead of owning those
responsibilities directly.

## Consequences

### Positive

- `client.py` is more clearly a facade/orchestrator
- HTTP transport becomes reusable and independently testable
- ontology artifact promotion stops accumulating as ad hoc client glue

### Negative

- `_LocalEngine` still lives in `client.py` for now
- follow-up slices are still required to finish the client split

## Follow-up

- move local engine internals behind a dedicated module
- continue shrinking `client.py` as canonical query/agent/ontology engines land
