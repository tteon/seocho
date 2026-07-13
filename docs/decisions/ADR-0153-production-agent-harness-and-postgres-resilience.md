# ADR-0153: Production agent harness and PostgreSQL resilience controls

Date: 2026-07-14
Status: Accepted

## Context

SEOCHO now has live blockchain long-term-memory workloads and an SDCR evidence
swarm, but a production agent harness needs more than retrieval fan-out. Agents
that read memory and call tools need identity, bounded delegation, tool-boundary
policy, versioned harness evaluation, and database overload controls that fail
predictably under cache misses, background jobs, and expensive query spikes.

The PostgreSQL scaling lessons we apply are intentionally application-side:
reduce primary pressure, isolate critical and background work, coalesce cache
miss storms, route bounded-staleness reads by freshness, shed known expensive
query shapes, and guard online schema changes. Physical HA, streaming replicas,
PgBouncer, and cascading replication require separate deployment qualification.

## Decision

Add first-class agent principals with scoped actions, resources, expiry, and
bounded delegation. Add a tool-boundary guard that authorizes tool inputs and
filters protected evidence outputs before synthesis. Add versioned harness
manifests and rubric promotion gates so prompt/model/ontology/retrieval changes
can be evaluated as candidates before promotion.

Extend the evidence swarm with bounded iterative retrieval. It retries missing
slots against alternative authorized views, records attempt counts, and returns
a structured unknown when the attempt or specialist-call budget is exhausted.

Add PostgreSQL resilience primitives under `src/seocho/memory/`:

- workload-tier admission control for critical, interactive, and background
  work;
- single-flight cache fills to prevent cache-miss storms from multiplying
  identical database reads;
- freshness-aware PostgreSQL read routing contracts;
- bounded retry budgets;
- query digest blocking for emergency load shedding;
- schema-change inspection that allows lightweight online operations and blocks
  rewrite-prone DDL by default.

Expose low-cardinality metrics for authorization, iterative retrieval attempts,
PostgreSQL admission, admission wait, single-flight outcomes, and read routes.
Add Grafana panels to the memory-consistency dashboard.

## Consequences

Critical reads can be protected from background work without changing the
authoritative memory schema. The cache layer can prove that N concurrent misses
produce one database load for a key. Query and schema safety controls are
deterministic and auditable, not model-judged.

The read-router contract is not a claim that physical replicas exist in the
local benchmark. A single-primary container can validate routing decisions,
admission, pooling, and cache behavior; it cannot qualify failover, replica lag,
PgBouncer behavior, or cascading replication. Those remain deployment-profile
tests.
