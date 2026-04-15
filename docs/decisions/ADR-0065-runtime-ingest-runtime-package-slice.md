# ADR-0065: Move RuntimeRawIngestor To runtime/ Package

Date: 2026-04-15
Status: Accepted

## Context

`RuntimeRawIngestor` had already adopted canonical SEOCHO seams for extraction,
linking, runtime memory shaping, and semantic-artifact helpers, but the module
still lived under `extraction/runtime_ingest.py`.

That package path no longer matched the role of the module:

- it is runtime-only behavior, not reusable SDK extraction logic
- it participates in deployment-shell composition through
  `server_runtime.py`, `memory_service.py`, and API endpoints
- it should move with the staged `extraction/ -> runtime/` rename rather than
  remain as a misleading exception

## Decision

Move the canonical owner of `RuntimeRawIngestor` to
`runtime/runtime_ingest.py`.

Keep `extraction/runtime_ingest.py` as a compatibility alias that re-exports
the canonical runtime module.

Update repo-owned tests, CI, and active docs to prefer
`runtime.runtime_ingest`.

## Consequences

### Positive

- runtime shell ownership becomes more internally consistent
- active documentation and tests now reinforce `runtime/` as the canonical
  deployment package
- future `runtime_ingest` cleanup can happen without reintroducing new logic in
  `extraction/`

### Negative

- temporary aliasing increases bridge surface until downstream imports migrate
- `runtime/__init__.py` still bootstraps the historical `extraction/` flat
  module path for remaining runtime helpers

## Out of Scope

- broad `runtime_ingest` orchestration redesign
- moving historical helper modules like `raw_material_parser.py`
- deprecating or removing `extraction/runtime_ingest.py` immediately

## Follow-up

- continue moving remaining runtime-only callers to canonical `runtime/*`
  imports
- keep shrinking `RuntimeRawIngestor` toward deployment-shell orchestration
  only
- remove the compatibility alias once downstream imports and shims are retired
