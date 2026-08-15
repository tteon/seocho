# ADR-0159: scheduler v2 — p99-first, and what the probes actually said

Date: 2026-08-15 · Status: accepted (measurement record)

## Context

Two critiques of the v1 admission layer (hadry): tail latency (p99) is the
metric, not p50 — and by p99, v1's FIFO gate made the light class *worse*
than no gate at all (1,767ms vs 239ms, E1) via head-of-line blocking behind
100x-heavier calls; and the mechanisms needed an organizing principle.
Scheduler v2 (design note scheduler-v2-design.md) answered with three:
variance isolation (size lanes), work conservation (borrowable reserve),
fast structured failure (predicted-wait rejection). This ADR records what
the live probes then did to that design.

## What the first probe run caught (kept on record deliberately)

- **Estimate poisoning starves the polite.** The first predicted-wait
  implementation used a global max service EWMA; one 8-second warm-up
  observation contaminated the estimate for an 80ms-traffic lane, and
  fast-fail then rejected every interactive high-class arrival instantly —
  0/85 served, a *total* starvation of exactly the class the scheduler
  exists to protect. Fix: the wait predictor uses a per-lane EWMA of what
  actually ran in that lane. The failure mode generalizes: fast-fail is
  only as safe as its estimator, and polite (intermittent, deadline-bound)
  callers are the first casualties of a bad one.
- Rejection storms: fast-fail assumes callers back off on a structured
  rejection; a retry-without-backoff loop turns zero-wait rejection into a
  100k-call/s storm. The probes now model agent-loop backoff explicitly.

## E2 — lanes vs fast-fail (raw: ADR-0159-scheduler-v2-probes.json)

8 light + 4 heavy concurrent sessions, SF10, cap 4, backoff on rejection:

| arm | light p99 | heavy served |
|---|---|---|
| single lane + per-lane fast-fail | **122ms** | 4 |
| lanes (light 2 / heavy 2) | 565ms | 5 |

With a *correct* wait estimator, fast-fail alone keeps the light class's
p99 at uncontended levels — heavy arrivals that would queue get rejected
and back off before they can occupy. Static lane partitioning then only
*halves* the light class's capacity and pays a partition tax. **Data-driven
default: single lane + fast-fail (light_permits=0 stays the default);
lanes remain opt-in** for regimes where heavy callers do not back off.

## S2 — static vs work-conserving reserve (same file)

12-normal flood + 2 interactive high, reserved=2, two phases:

| | high (active) | normal (high active) | normal (high idle) |
|---|---|---|---|
| static reserve (v1) | 64/64, p99 129ms | 169 served | 190 served |
| work-conserving (v2) | 54/56, p99 173ms | **263 served (+56%)** | **302 served (+59%)** |

Equal protection of the interactive class, and the reserve stops taxing
normal throughput when unused — work conservation delivering its exact
claim. The explicit trade: borrowing admits more normals, so their own
p95/p99 deepens (queueing among themselves); served-rate up, tail up,
both visible.

## Consequences

- Defaults: single lane + per-lane fast-fail + borrowable reserve;
  lanes opt-in. All knobs on ``Seocho(...)`` constructor, off by default.
- The estimator-poisoning finding joins the paper's trust/execution
  narrative: a scheduler's safety claims inherit its estimator's failure
  modes.
