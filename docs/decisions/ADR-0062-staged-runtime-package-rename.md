# ADR-0062: Staged Runtime Package Rename

## Status

Accepted

## Context

`extraction/` started as an ingestion-oriented package, but it gradually
accumulated runtime server responsibilities:

- route wiring
- policy checks
- runtime service composition
- public memory APIs
- compatibility shims for canonical `seocho/*` logic

That package name now obscures the actual boundary between:

- canonical engine logic under `seocho/`
- deployment shell logic around HTTP/runtime behavior

## Decision

We will adopt a staged rename from `extraction/` toward `runtime/`.

Boundary contract:

- `seocho/` owns canonical business logic
- `runtime/` will become the canonical deployment shell
- `extraction/` will shrink toward extraction-only concerns or temporary
  compatibility wrappers

We choose `runtime/` instead of `server/` because the target shell includes
HTTP concerns plus readiness, policy, registry, and deployment behavior that is
broader than a single server entrypoint.

## Consequences

Positive:

- clearer SDK vs runtime ownership
- less naming drift in docs and code review
- easier parity reasoning between local SDK and runtime entrypoints
- cleaner path for eventually removing `extraction/` shims

Negative:

- staged compatibility work is required
- downstream imports must be preserved or migrated carefully
- docs, tests, and tracker state must stay aligned over a longer migration

## Implementation Notes

Migration stages:

1. freeze new business logic in `extraction/`
2. introduce `runtime/` as canonical shell
3. move thin runtime modules first
4. separate extraction-only concerns
5. deprecate and remove wrappers when downstreams are ready

The shipped plan is documented in `docs/RUNTIME_PACKAGE_MIGRATION.md`.
