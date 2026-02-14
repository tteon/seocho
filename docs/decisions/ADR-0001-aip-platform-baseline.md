# ADR-0001: AIP Platform Baseline

- Status: Accepted
- Date: 2026-02-14
- Deciders: SEOCHO team

## Context

SEOCHO will evolve from agent orchestration components into a productized
agent platform with a frontend-driven user experience.

The team needed one baseline decision set for:

- agent runtime stack
- tracing/observability
- backend graph database
- tenancy model for MVP
- policy/ontology reasoning strategy

## Decision

1. Agent runtime: **OpenAI Agents SDK**
2. Tracing and evaluation: **Opik**
3. Backend graph database: **DozerDB only** (fixed for now)
4. Tenancy: **Single-tenant MVP**, but design for future multi-tenant expansion
5. Ontology/policy reasoning:
   - runtime authorization via app-level policy (RBAC/ABAC)
   - `owlready2` is allowed only in offline validation/compile flow
   - do not put `owlready2` reasoning in hot request path

## Rationale

- OpenAI Agents SDK + Opik already aligns with current implementation.
- Fixing DozerDB reduces integration complexity during MVP.
- Single-tenant first improves delivery speed and lowers operational risk.
- Offline ontology reasoning keeps latency and memory predictable.

## Consequences

Positive:

- faster implementation and fewer moving parts
- clear ownership of observability and runtime stack
- easier debugging and onboarding

Trade-offs:

- no tenant isolation in v1 runtime
- future migration required for full multi-tenant controls
- ontology reasoning richness deferred to async/offline jobs

## Implementation Guardrails

- Include `workspace_id` in domain models now, even in single-tenant mode.
- Keep authz checks centralized in API/service layer.
- Keep policy artifacts serializable (JSON/SHACL-like), versioned, and cacheable.
- Record all future architecture changes as ADRs under `docs/decisions/`.

## Follow-up ADRs

- ADR-0002: Multi-tenant isolation strategy (schema and auth boundaries)
- ADR-0003: DozerDB governance mapping (constraints, indexing, backups)
- ADR-0004: Rule pipeline export targets (SHACL + DB-native constraints)
