# ADR-0052: Agent Server Runtime Service Split First Slice

- Status: Accepted
- Date: 2026-04-13

## Context

`extraction/agent_server.py` had accumulated:

- FastAPI route registration
- request and response models
- eager singleton construction for runtime services
- public-memory router composition
- runtime utility helpers

That made the entrypoint large and increased import-time side effects.

## Decision

Create `extraction/server_runtime.py` as the shared runtime service composition
module for the server path.

The first slice moves:

- server context
- lazy runtime service getters
- shared runtime utility helpers
- graph/schema helper functions

The public memory router is also updated to accept a lazy memory-service getter
so `agent_server.py` no longer has to instantiate the memory service at import
time.

## Consequences

### Positive

- `agent_server.py` becomes a thinner transport entrypoint
- shared runtime services can be imported without booting the full server
- import-time side effects are reduced

### Negative

- request/response models still live in `agent_server.py` for now
- more route wiring is still embedded in the entrypoint and will need later
  slices

## Follow-up

- continue moving request/response schemas into clearer server-side modules
- keep server runtime logic as composition over canonical `seocho/*` engines
