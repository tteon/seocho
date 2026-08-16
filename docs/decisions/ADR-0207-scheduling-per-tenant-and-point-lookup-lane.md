# ADR-0207: Scheduling — per-tenant isolation is structural; point lookups take the light lane (D4)

- Status: accepted
- Date: 2026-08-16
- Tickets: seocho-ia4 (structured runtime), the multi-agent-flow review (#5)
- Related: ADR-0158/0159 (LaneScheduler), ADR-0203 (read-side resolver)

## Context

The flow review's blocker #5 said the scheduler "has no tenant dimension … one tenant's N
heavy fan-out reads monopolize the heavy lane and starve co-tenants." Verified against
origin/main, that claim does not hold for this model: `LaneScheduler` is created **per
`SeochoOS` instance**, and `SeochoOS` is **one instance per `(workspace, database)`**. So each
tenant already has its **own** scheduler and its own permit pool — one tenant's storm consumes
only its own permits; it cannot starve a co-tenant. Cross-tenant fairness is **structural by
the instance model**, not a shared-scheduler accounting gap. (This is the same stale-tree
over-reading pattern as the review's refuted #1 and #6-resolver claims.)

The *real* residual is **within** a workspace: a fan-out issues a burst of cheap
id-equality + `LIMIT 1` canonical-id resolves (exactly the ADR-0203 read-side resolver
shape); under `LaneScheduler`'s "unknown means heavy" EWMA cold-start, each is classified
heavy on first sight and floods the protected heavy lane precisely when the fan-out is widest.

## Decision

- **Document** that cross-tenant scheduling isolation is structural (per-`(workspace,
  database)` `SeochoOS` → per-workspace `LaneScheduler`); do not build a redundant
  per-workspace permit cap. Disclose the honest residual: there is no *global* cross-tenant
  admission ceiling across many instances in one process — a deliberate isolation-over-global-
  cap choice (a process-wide ceiling is future work if hosted density demands it).
- **Route cheap point lookups to the light lane** directly: `execute_query` classifies an
  id-equality + `LIMIT 1` statement (no aggregation/ordering) as `light` when a light lane
  exists, bypassing the "unknown means heavy" cold-start. `LaneScheduler.has_light_lane` gates
  it; everything else keeps the observed per-statement EWMA classification.

## Consequences

- A fan-out's canonical-id resolves no longer flood the heavy lane on first sight, so the
  protected heavy lane stays available for genuinely heavy reads — the within-workspace
  scheduling-quality fix the review's minor identified, and it composes with D2/D3 (the
  read-side resolver's lookups are the exact shape now fast-pathed).
- The OS scheduling story is now stated precisely: **cross-tenant = separate instances
  (isolation), within-tenant = light/heavy lanes + high/normal reserve + EWMA, with cheap
  point lookups seeded light.**
- 3 tests: point-lookup shape detection, `has_light_lane` gating, structural per-tenant
  scheduler isolation. operating-layer/admission suites green (31); `ruff` clean.
