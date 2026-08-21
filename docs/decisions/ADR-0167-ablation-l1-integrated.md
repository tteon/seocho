# ADR-0167: ablation L1 — integrated OS-vs-bare, one mixed 2-tenant load

Date: 2026-08-15 · Status: accepted (measurement record) · seocho-41a

## Context

Level-1 headline of the OS study (wiki/os-ablation-study-design.md). Level-2
(ADR-0160/0161/0162, 0158/0159, 0164, 0165, 0166) measured each subsystem alone.
L1 asks whether the guarantees hold TOGETHER: one mixed, concurrent, two-tenant
workload run through a BARE path (raw graph tool, no governance) vs the OS path
(`SeochoOS.execute_query`: admission + tenancy pin + scope enforcement + row-cap
disclosure). Task-correctness (does an LLM agent still answer right?) needs an
agent + judge and is the follow-up; this measures the governance axes on real
data.

## Method

`scripts/agentos/ablation_l1_integrated.py`, live DozerDB. Two-tenant node set
(120 `acme` + 120 `globex`, probe label, cleaned up). 12 workers × 8 queries,
each alternating a scoped-but-over-cap read (disclosure axis) and an adversarial
cross-tenant read (isolation axis), all nominally scoped to `acme`. OS arm:
`max_inflight=4`, `row_cap=50`, `admission_wait_s=2`. A counting wrapper observes
max store concurrency.

## Result (the outcome vector)

| axis | BARE | OS |
|---|---|---|
| cross-tenant leaks | **4,800** | **0** |
| truncation disclosure rate | **0.0** | **1.0** |
| max store concurrency | 12 | **4** (admission-bounded) |
| admission rejected | 0 | 0 (queued, served) |
| p99 ms | 155 | 272 |

- **Isolation composes:** under concurrent adversarial load the bare path leaks
  4,800 cross-tenant rows; the OS leaks 0. The A2 result holds under a realistic
  mixed workload, not just single queries.
- **Honesty composes:** every over-cap result is disclosed by the OS (1.0) and
  silently truncated by the bare tool (0.0).
- **Scheduling composes:** the OS holds store concurrency at 4 (its `max_inflight`)
  while the bare path drives all 12 workers at the database at once.
- **The disclosed cost:** OS p99 is higher (272 vs 155ms) — the concurrency
  bound queues excess workers, and that queueing is the tail. This is the honest
  trade: at N=12 on a healthy DB the bound only adds latency; its benefit (not
  melting the database) shows at scale, where unbounded fan-out is the failure.
  The tail is the scheduling pillar's optimization target (ADR-0159), not a
  regression to hide.

## Consequences

- L1 completes the ablation study: Level-2 A1–A6 each isolated, and L1 shows they
  compose under one load — the OS's guarantees (0 leaks, full disclosure, bounded
  concurrency) hold together, at a disclosed p99 cost. This is the F3 figure.
- Remaining: the task-correctness parity axis (agent + judge) — the claim that OS
  wins the governance axes at *equal* answer quality — is the LLM-agent follow-up
  (seocho-41a keeps that open).
