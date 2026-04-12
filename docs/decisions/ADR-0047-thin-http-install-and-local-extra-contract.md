# ADR-0047: Thin HTTP Install And Local Extra Contract

- Status: Accepted
- Date: 2026-04-13

## Context

SEOCHO now exposes two materially different Python usage modes:

1. thin HTTP client mode
2. local SDK engine mode with ontology + graph store + provider setup

The documentation had already started to distinguish those modes, but the
package surface was still muddy:

- `pip install seocho` was described as a thin HTTP-mode install
- local engine usage was documented through `pip install -e ".[dev]"`
- the top-level `seocho` import eagerly pulled optional local/runtime modules,
  which weakened the thin-install contract

This made onboarding confusing and made the published package harder to reason
about.

## Decision

We standardize the Python install contract as:

- `pip install seocho`
  - thin HTTP client mode
- `pip install "seocho[local]"`
  - published-package local SDK engine mode
- `pip install -e ".[dev]"`
  - repository development
- `pip install "seocho[ontology]"`
  - offline ontology governance helpers

To make the thin install contract real:

- top-level `seocho` exports move to lazy loading
- optional local/runtime modules are imported only when their symbols are
  actually accessed
- onboarding docs and mirrored website docs must reflect the same install split

## Consequences

Positive:

- `pip install seocho` can stay intentionally small
- local engine users get a clear published-package path
- docs become more honest about when DozerDB/Neo4j is required
- website and source repo can explain the same runtime-mode split cleanly

Trade-offs:

- top-level package export logic is slightly more indirect because it uses lazy
  attribute loading
- install guidance is more explicit and therefore longer

## Follow-up

- keep validating that new top-level exports do not accidentally reintroduce
  eager optional imports
- if a future auto-generated API doc tool is added, keep it aligned with the
  same install/runtime-mode split rather than collapsing everything back into a
  single “just pip install it” story
