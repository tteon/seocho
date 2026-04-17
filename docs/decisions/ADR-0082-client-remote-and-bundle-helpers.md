# ADR-0082: Client Remote And Bundle Helpers

## Status

Accepted

## Context

After moving `_LocalEngine` out of `seocho/client.py`, the facade still owned
two helper concerns directly:

- HTTP transport setup and request dispatch
- runtime bundle import/export glue

Both concerns are part of the SDK facade surface, but neither should keep
growing inside `client.py`.

## Decision

SEOCHO will keep `Seocho` as the public SDK facade and move the following
helper ownership behind dedicated modules:

1. `seocho/client_remote.py`
   - owns `RuntimeHttpTransport` wiring and `request_json` dispatch through
     `RemoteClientHelper`
2. `seocho/client_bundle.py`
   - owns runtime bundle export/import glue through
     `RuntimeBundleClientHelper`

Rules:

1. `Seocho` remains the stable public entrypoint.
2. `client.py` may compose these helpers, but should not re-own their logic.
3. Basic CI and module-ownership checks should enforce the boundary.

## Consequences

Positive:

- `client.py` continues shrinking toward a true facade
- transport and bundle glue become independently testable
- future remote helper decomposition has a stable landing zone

Negative:

- more internal helper files to navigate
- some method bodies in `client.py` still delegate one-by-one until a later
  remote surface extraction lands

## Implementation Notes

- ownership map: `docs/MODULE_OWNERSHIP_MAP.md`
- seam doc: `docs/INTERNAL_CLASS_DESIGN.md`
- enforcement: `scripts/ci/check-module-ownership-contract.sh`
