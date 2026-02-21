# ADR-0020: P1 ElementId Migration, Health Split, and Readiness Fallback

- Date: 2026-02-21
- Status: Accepted

## Context

P1 priorities required practical improvements in three areas:

1. query durability against DozerDB/Neo4j deprecation (`id(...)` warnings)
2. runtime vs batch process visibility for operations
3. readiness-aware orchestration behavior when debate agents are unavailable

## Decision

1. Migrate semantic runtime query paths from `id(...)` to `elementId(...)`.
2. Add split health endpoints:
   - `GET /health/runtime`
   - `GET /health/batch`
3. Introduce readiness state summary for debate execution:
   - `ready`, `degraded`, `blocked`
   - include `debate_state` and `agent_statuses` in debate response
4. Add platform-level fallback:
   - if requested mode is `debate` and `debate_state=blocked`, execute semantic mode automatically
   - return fallback metadata in payload (`runtime_control`, `fallback_from`)
5. Persist pipeline batch status from container entrypoint via `SEOCHO_BATCH_STATUS_FILE`.

## Consequences

Positive:

- reduced exposure to deprecated ID semantics
- clearer operational diagnosis with split health surfaces
- better user continuity when debate mode is temporarily unavailable

Tradeoffs:

- additional response metadata and orchestration branch complexity
- entrypoint now manages an extra status artifact file

## Implementation Notes

Key files:

- `extraction/semantic_query_flow.py`
- `extraction/agent_server.py`
- `extraction/platform_agents.py`
- `extraction/agent_readiness.py`
- `extraction/entrypoint.sh`
- tests under `extraction/tests/`
