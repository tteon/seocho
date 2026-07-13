# Production agent harness and PostgreSQL resilience controls

This ExecPlan is a living document. The sections `Progress`, `Surprises &
Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to
date as work proceeds.

This plan follows `.PLANS.md` from the repository root and is tracked by Beads
item `seocho-1yf`.

## Purpose / Big Picture

SEOCHO should demonstrate the infrastructure around production agents, not only
the model call. After this work, a blockchain-memory query can run under an
auditable agent principal, use bounded retrieval-as-a-subagent, pass tool input
and output policy, and protect PostgreSQL authority from cache-miss storms and
background workload pressure. Operators can see the controls in Grafana and
Tempo.

## Progress

- [x] (2026-07-14) Added agent principal/delegation, tool-boundary guard, and
  harness promotion gate.
- [x] (2026-07-14) Added bounded iterative evidence-swarm retrieval with
  structured unknown on budget exhaustion.
- [x] (2026-07-14) Added PostgreSQL workload admission, single-flight cache,
  read-router contract, retry budget, query digest policy, and schema guard.
- [x] (2026-07-14) Added unit tests and production metric specs.
- [x] (2026-07-14) Added a live PostgreSQL resilience benchmark and ran it
  against the existing agent-memory database.
- [x] (2026-07-14) Added Grafana panels for admission, wait, cache, and routes.

## Surprises & Discoveries

- The existing `QueryProxy` already has generic retrieval admission control, so
  the new code targets PostgreSQL authority and workload tiers instead of
  duplicating that path.
- The local environment has a real 1.244M-revision PostgreSQL authority store,
  but no physical streaming replica. Replica routing can be tested as a
  deterministic contract; failover and lag cannot be claimed from this run.
- A shared four-connection pool plus background `pg_sleep` created critical
  read p95 around 400 ms, making workload isolation visible even in a small
  local run.

## Decision Log

- Decision: Treat agent identity as a typed principal, not an opaque API key.
  Rationale: actions need scoped resources, expiry, delegation, and audit
  receipts.
  Date/Author: 2026-07-14 / Codex
- Decision: Keep automatic harness improvement candidate-only.
  Rationale: production promotion needs rubric gates, canary evidence, and
  rollback, not silent prompt/model replacement.
  Date/Author: 2026-07-14 / Codex
- Decision: Implement application-side PostgreSQL controls before claiming
  physical HA.
  Rationale: cache stampede, workload isolation, query blocking, and schema
  guardrails are testable in the current environment; cascading replication is
  not.
  Date/Author: 2026-07-14 / Codex

## Outcomes & Retrospective

The live run `seocho.okx-postgres-resilience-live.v1` used PostgreSQL 18.4 with
1,244,329 memory revisions across 34 workspaces. A 64-way cache-miss storm
generated one database loader call, with 63 coalesced waiters and a 98.44%
coalescing ratio. The critical-read p95 under a shared pool was 400.68 ms; with
tier isolation it was 6.40 ms, a 62.63x ratio in this closed workload. The
isolated path admitted two background jobs and rejected thirty, while all 32
critical reads completed.

Tempo stored root trace `685a16cf2e2c8c9ee6d61a373f688dec` for
`okx.postgres_resilience.run`. Prometheus exposed the new
`seocho_postgres_*` metrics. The run explicitly reports physical replication
as `not-qualified` because the local service is a single primary.
