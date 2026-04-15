# ADR-0075: Embedded Local Default Install Contract

Date: 2026-04-15
Status: Accepted

## Context

SEOCHO's product wedge is ontology-aligned agent-to-graph middleware: users
should index simple text while the SDK keeps agent reasoning, ontology policy,
validation, and graph writes aligned.

The old onboarding story still implied that a Neo4j or DozerDB daemon was
required before a hello world could run. That made the first five minutes too
heavy compared with other agent-memory libraries.

Recent code introduced `LadybugGraphStore` as the default embedded backend for
`Seocho.local(ontology)`, but the packaging and docs still described local mode
as Neo4j-first.

## Decision

Treat `Seocho.local(ontology)` as the official serverless local hello-world
path. It uses embedded LadybugDB at `.seocho/local.lbug` unless the caller
passes a Bolt URI or an explicit `graph_store`.

`seocho[local]` must include the dependency required by that default path. The
published local install path should therefore include `real_ladybug` alongside
the existing local runtime and provider dependencies.

Expose `LadybugGraphStore` from both `seocho.store` and the compatibility
`seocho.graph_store` module because it is now part of the public local SDK
surface.

DozerDB/Neo4j remains the production graph path. Kuzu remains a strong
candidate for a future optional backend, but it should be evaluated in a
separate backend slice rather than replacing the just-landed default.

## Consequences

- `pip install "seocho[local]"` supports the default local graph backend.
- New users can run `Seocho.local(ontology)` without starting Docker or Neo4j.
- Production examples still show `Neo4jGraphStore` for DozerDB/Neo4j over Bolt.
- The public docs now match the implementation instead of promising a
  Neo4j-first default that no longer exists.
- Kuzu evaluation can focus on dialect compatibility, maintenance status,
  benchmark behavior, and GraphStore abstraction fit without blocking
  onboarding.

## Related Documents

- `README.md`
- `docs/PYTHON_INTERFACE_QUICKSTART.md`
- `docs/decisions/ADR-0047-thin-http-install-and-local-extra-contract.md`
