# ADR-0071: Runtime Agent Ontology Middleware Contract

Date: 2026-04-15
Status: Accepted

## Context

ADR-0068 through ADR-0070 introduced compact ontology context descriptors,
graph-write metadata, query-time guardrails, and typed runtime response fields.
The remaining contract gap was the agent-facing runtime surface: router,
debate, execution-plan, and platform chat calls could still hide ontology
context status inside trace/tool payloads or nested runtime data.

This weakened the library user experience because application code had to know
which endpoint produced which metadata shape. It also weakened the agent-to-DB
middleware story because graph routing and ontology parity were not surfaced as
one consistent response contract.

## Decision

Expose `ontology_context_mismatch` as a top-level typed field on router,
debate, platform chat, and execution-plan SDK responses.

When router mode receives `graph_ids`, resolve those graph IDs to databases and
bind the agent tool context to those databases. This makes the requested graph
scope affect both database access and ontology parity metadata.

Platform semantic chat may resolve `graph_ids` to databases when explicit
databases are not provided, so the frontend-facing route can reuse the same
graph selection language as router and debate calls.

## Consequences

- Library users can inspect ontology/database drift the same way across
  `semantic(...)`, `chat(...)`, `search_with_context(...)`, `advanced(...)`,
  `react(...)`, `plan(...).run()`, and `platform_chat(...)`.
- Router agent tools now honor explicit graph scopes instead of treating
  `graph_ids` only as request metadata.
- The field remains a guardrail, not a hard blocker. A mismatch warns users
  that a graph may need re-indexing under the active ontology profile.
- This does not introduce heavy ontology reasoning in the runtime hot path.
  The middleware reads compact graph metadata already written during ingest.

## Follow-Ups

- Keep extending evidence-bundle and intent-slot contracts separately from this
  middleware field.
- Benchmark graph-load and query paths before introducing Rust or portable
  in-memory formats for ontology context transport.
