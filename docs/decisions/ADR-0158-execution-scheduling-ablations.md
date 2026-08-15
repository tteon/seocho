# ADR-0158: E1/S1 — the execution layer's ablations, measured

Date: 2026-08-15 · Status: accepted (measurement record)

## Context

The execution and scheduling pillars of the operating-layer thesis carried
implemented controls (ADR-0153, ADR-0157) but no ablation: with-layer vs
without-layer had never been run as an experiment. E1 and S1 close that,
on live FinBench graphs (graphstack/dozerdb 5.26.3.0, SF1/SF10), with the
governed path being exactly `AgentOS.execute_query`.

## E1 — admission on vs off under concurrency

`scripts/agentos/e1_ablation.py`; raw cells in
`ADR-0158-e1-execution-ablation.json` (16 cells: SF1/SF10 x light/heavy x
N in {4,16} x governed/ungoverned).

| SF10 cell | ungoverned | governed (cap 4, 10s deadline) |
|---|---|---|
| light, 16 sessions | p50 **223.7ms** — contention taxes *every* call; store concurrency 16 | p50 **93.7ms** = single-session latency; queueing isolated to the p95 tail (1.2s) |
| heavy, 16 sessions | p50 **21.8s**, all 96 served, wall 132s — uniform collapse | p50 **8.0s** (= the 4-way baseline), **32 served + 64 structured rejections**, wall 64s |

The correct claim, stated carefully: the layer does not make contention
free — it makes the trade **visible and configurable**. Admitted work runs
at healthy latency; excess demand becomes an explicit, machine-actionable
rejection instead of a uniform slowdown nobody chose. The store-side
concurrency bound held in every governed cell. The flip side is on record:
with the deadline below queue-drain time, served-rate drops — the
`admission_wait_s` knob is the policy, not the layer.

## S1 — does the priority reserve prevent starvation?

`scripts/agentos/s1_fairness.py`; raw arms in `ADR-0158-s1-fairness.json`
(12 normal sessions flooding continuously + 2 interactive high sessions,
20s per arm, cap 4).

| | high class | normal class |
|---|---|---|
| reserved=0 | **34/36 timeouts — 94% starved** (p50 of the 2 served: 551ms) | 908 served, p50 88ms |
| reserved=2 | **166/166 served, zero timeouts, p50 92.4ms** (= uncontended) | 450 served (the price, explicit), p50 89ms |

Jain fairness over per-session served counts inside the normal class is
1.0 in both arms: the reserve protects the high class without skewing
normals against each other. Starvation is not tuned away; it is
structurally prevented by capacity no normal call may occupy
(`PriorityAdmission`, seocho.agentos), at a visible throughput price.

## Consequences

- The execution and scheduling pillars now carry ablation evidence in the
  focused paper plan (spine: the governed schema contract; these are §3).
- `reserved_for_high` ships on `AgentOS` (default 0 — plain bounded
  admission), with the reserve semantics unit-tested.
- Full multi-class fairness (aging, weighted shares) stays out of scope
  until a workload demands it; the two-class reserve is the smallest
  mechanism the data justifies.
