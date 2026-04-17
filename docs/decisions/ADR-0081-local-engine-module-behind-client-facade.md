# ADR-0081: Local Engine Module Behind Client Facade

## Status

Accepted

## Context

`seocho/client.py` is the public SDK facade, but local-mode orchestration was
still implemented inside that file as `_LocalEngine`.

That made one file own two different concerns:

- the stable public constructor and method surface
- the canonical local indexing/query orchestration logic

The previous seam work in ADR-0080 introduced internal classes such as
`IngestionFacade`, but the local engine itself still lived in the facade file.

## Decision

SEOCHO will move `_LocalEngine` into `seocho/local_engine.py` and keep
`seocho/client.py` as a composition-oriented public facade.

Rules:

1. `Seocho` remains the public SDK entrypoint.
2. Local indexing and local query orchestration live in `seocho/local_engine.py`.
3. `client.py` may import `_LocalEngine`, but it must not define that class.
4. Basic CI and module-ownership checks must enforce this boundary.

## Consequences

Positive:

- shrinks `client.py` toward a true facade
- gives future local-mode decomposition a stable landing zone
- reduces the chance that new business logic bypasses the canonical seam docs

Negative:

- one more internal file for contributors to learn
- temporary duplication risk if future local helpers are added back into
  `client.py` without ownership checks

## Implementation Notes

- design doc: `docs/INTERNAL_CLASS_DESIGN.md`
- enforcement: `scripts/ci/check-module-ownership-contract.sh`
- first moved object: `_LocalEngine`
