# ADR-0028: Graph Registry And Multi-Instance Debate Runtime

Date: 2026-03-12
Status: Accepted

## Context

SEOCHO debate mode previously assumed one Neo4j or DozerDB instance with multiple databases beneath it. That was sufficient for early experiments, but it is too narrow for the intended graph-memory interface.

The product direction now requires:

- graph-scoped OpenAI Agents SDK specialists
- the ability for different graph IDs to point at different Neo4j instances
- lightweight public discovery of graph targets
- a mem0-like interface layer without exposing internal agent topology

## Decision

SEOCHO will introduce a graph-scoped runtime contract:

1. add `graph_registry` as the control-plane source of truth for graph targets
2. keep `db_registry` as a compatibility layer for database validation
3. bind debate agents to `graph_id -> uri/auth/database/ontology_id/vocabulary_profile`
4. run debate fan-out by `graph_id`, not only by database name
5. expose `GET /graphs` as the public graph discovery surface
6. allow `graph_ids` in runtime debate requests and public chat-style interfaces

## Consequences

Positive:

- each agent can be hard-bound to one graph target by tool closure
- multi-instance graph debate is supported without prompt-only scoping
- ontology and vocabulary metadata can travel with graph routing
- mem0-style client flows can discover graph targets safely

Tradeoffs:

- runtime configuration becomes more explicit and therefore more operationally visible
- graph provisioning and ingestion still need additional surface work to become fully graph-target-native everywhere
- legacy database-oriented endpoints remain in place during the transition

## Implementation Notes

- default graph targets are loaded from `extraction/conf/graphs/default.yaml`
- override path uses `SEOCHO_GRAPH_REGISTRY_FILE`
- `MultiGraphConnector` caches drivers per `(uri, user, password)`
- `AgentFactory` now creates graph specialists with graph profile tools
- `DebateOrchestrator` returns both `graph` and `db` in debate results
