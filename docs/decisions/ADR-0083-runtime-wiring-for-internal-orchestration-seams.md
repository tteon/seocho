# ADR-0083: Runtime Wiring For Internal Orchestration Seams

## Status

Accepted

## Context

ADR-0080 introduced `DomainEvent`, `IngestionFacade`, `QueryProxy`,
`AgentFactory`, and `AgentStateMachine` as the modular-monolith seams for
SEOCHO.

That decision was intentionally additive. Some seams existed, but important hot
paths still bypassed them:

- runtime graph reads in `runtime/memory_service.py` opened Neo4j sessions
  directly
- runtime Cypher tool execution in `runtime/agent_server.py` called the legacy
  connector directly
- semantic flow construction in `runtime/server_runtime.py` instantiated
  `SemanticAgentFlow` directly instead of going through the canonical
  `seocho.query.AgentFactory`
- readiness summaries were string-based helpers instead of normalizing onto the
  explicit `AgentStateMachine`

At the same time, the multi-agent debate specialist factory still lives on the
legacy `extraction/agent_factory.py` path and cannot be converged casually
without wider behavior risk.

## Decision

SEOCHO will wire the existing orchestration seams into the first runtime hot
paths without changing the public SDK or runtime API surface.

Rules:

1. `seocho/local_engine.py` remains the local ingest entrypoint behind
   `IngestionFacade`.
2. Runtime graph reads and Cypher tool execution must pass through
   `seocho.query.QueryProxy`.
3. `runtime/server_runtime.py` must construct the shared
   `SemanticAgentFlow` through the canonical `seocho.query.AgentFactory`.
4. `runtime.agent_readiness` must normalize debate availability through
   `runtime.agent_state.AgentStateMachine`.
5. The legacy debate specialist factory in `extraction/agent_factory.py`
   remains a temporary compatibility surface for now.
6. `LLMProxy` is explicitly deferred to a later slice; it is not part of this
   runtime wiring decision.

## Consequences

Positive:

- runtime read/query behavior now passes through the same instrumentation seam
  used by canonical query code
- semantic-flow construction has a canonical factory boundary in the runtime
  composition root
- debate readiness state is backed by the same explicit state model documented
  for degraded and blocked behavior

Negative:

- runtime still contains a split factory story until debate specialists are
  migrated off the legacy path
- `LLMProxy`-level provider instrumentation remains deferred

## Implementation Notes

- runtime composition root: `runtime/server_runtime.py`
- runtime query read path: `runtime/memory_service.py`
- runtime Cypher tool path: `runtime/agent_server.py`
- readiness summary: `runtime/agent_readiness.py`
- seam design doc: `docs/INTERNAL_CLASS_DESIGN.md`
