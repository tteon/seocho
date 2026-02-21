# ADR-0019: Agent SDK Adapter and Debate Readiness Contract

- Date: 2026-02-21
- Status: Accepted

## Context

Runtime breakages were observed when OpenAI Agents SDK call signatures changed (`Runner.run(agent=...)` vs `Runner.run(starting_agent=...)`).

Debate mode also needed explicit runtime availability reporting when registered databases were missing/unreachable.

## Decision

1. Introduce an SDK adapter layer:
   - add `extraction/agents_runtime.py`
   - use adapter for agent execution and trace context in runtime paths
   - support signature compatibility between `starting_agent` and `agent`

2. Remove direct `Runner.run` dependency from runtime modules:
   - `extraction/agent_server.py`
   - `extraction/debate.py`

3. Expose debate readiness metadata:
   - `agent_factory.create_agents_for_all_databases` returns per-DB readiness status
   - debate response includes:
     - `agent_statuses`
     - `degraded`

4. Add contract tests:
   - SDK adapter signature compatibility tests
   - debate orchestration contract test using adapter abstraction
   - agent factory readiness/degraded status assertions

## Consequences

Positive:

- lowers blast radius of SDK signature changes
- improves resilience of debate path under partial DB availability
- provides explicit degraded-state visibility to UI and ops tooling

Tradeoffs:

- small abstraction/maintenance overhead for adapter layer
- API response payload grew for debate mode

## Implementation Notes

Key files:

- `extraction/agents_runtime.py`
- `extraction/agent_server.py`
- `extraction/debate.py`
- `extraction/agent_factory.py`
- `extraction/tests/test_agents_runtime.py`
- `extraction/tests/test_debate.py`
- `extraction/tests/test_agent_factory.py`
