# ADR-0076: Local Compose Bind Host And Password Contract

Date: 2026-04-15
Status: Accepted

## Context

The local compose stack is intended for developer workstations and local
experiments. Publishing graph, runtime, and observability ports on all host
interfaces by default makes those services reachable from outside the machine
when the host firewall or network allows it.

The previous DozerDB/Neo4j compose configuration also defaulted to
`NEO4J_PASSWORD=password`. That default is unsafe because the graph service
accepts network authentication and is a common scanning target.

## Decision

All compose-published local service ports must bind to
`${SEOCHO_BIND_HOST:-127.0.0.1}` by default.

`NEO4J_PASSWORD` is required for compose startup. The compose file must not
fall back to `password`, and `.env.example` should leave the value blank so a
copied `.env` fails closed until the developer sets a real local secret.

Developers who intentionally need LAN exposure may set `SEOCHO_BIND_HOST=0.0.0.0`,
but that is an explicit opt-in and should be paired with firewall rules and
rotated credentials.

## Consequences

- Local compose startup no longer exposes DozerDB/Neo4j, extraction, UI, or
  Opik profile ports to the LAN by default.
- Accidental reuse of `neo4j/password` is blocked at compose configuration
  time.
- CI and docs checks can still validate compose by supplying a temporary
  non-secret `NEO4J_PASSWORD` value.
- Existing local `.env` files that still contain `NEO4J_PASSWORD=password`
  should be rotated before restarting compose.

## Related Documents

- `docker-compose.yml`
- `.env.example`
