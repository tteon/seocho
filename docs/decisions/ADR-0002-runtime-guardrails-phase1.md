# ADR-0002: Runtime Guardrails Phase 1

- Status: Accepted
- Date: 2026-02-14
- Deciders: SEOCHO team

## Context

After baseline stack consensus, implementation started with minimal runtime
guardrails that should not block MVP velocity.

## Decision

1. Introduce `workspace_id` in API request model and server context.
2. Add runtime policy engine for app-level permission checks.
3. Keep ontology reasoning out of hot path via explicit offline placeholder.
4. Add DozerDB-first config keys while preserving Neo4j compatibility aliases.

## Implementation

- `extraction/agent_server.py`
  - `QueryRequest.workspace_id` added
  - `ServerContext.workspace_id` added
  - policy check hook for `/run_agent` and `/run_debate`
  - workspace metadata attached to trace/span

- `extraction/policy.py`
  - workspace ID validation
  - simple role/action authorization
  - offline ontology reasoning placeholder

- `extraction/config.py`
  - `DOZERDB_*` introduced as primary settings
  - `NEO4J_*` preserved as compatibility aliases

## Consequences

Positive:

- future multi-tenant path is opened without changing external API shape later
- runtime guardrails are explicit and testable
- DozerDB adoption starts without breaking current modules

Trade-offs:

- policy model is intentionally minimal (role/action only)
- full tenant-aware authorization still deferred
