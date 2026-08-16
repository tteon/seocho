# ADR-0192: CostAwareEvictionCache — O(log n) lazy-heap victim selection

- Status: accepted
- Date: 2026-08-16
- Tickets: seocho-ia4.12 (under epic seocho-ia4)
- Related: ADR-0180 (allocator eviction), ADR-0185 (pin-aware eviction)

## Context

The 4-reviewer panel flagged `CostAwareEvictionCache._evict_to_budget` as a
tail-latency cliff: for every byte-budget overflow it rebuilt a full
`candidates = [e for e in self._entries ...]` list and took `min(candidates,
key=self._priority)` **per victim** — O(n) per eviction, O(n·k) per admission
that evicts k entries, all under a single lock. Under the cache's own churn
(the multi-tenant skewed load it exists to handle) this is the pathological
case, not the corner case.

A second, subtler finding surfaced while fixing it: the original code recomputed
`_priority = self._age + freq·cost·boost/size` **fresh on every comparison**, so
the aging term `self._age` — added uniformly to every entry — cancelled out of
the `argmin`. The policy was therefore **cost-weighted LFU** in observable
behaviour (a hot/expensive/shared page survives churn indefinitely), *not* the
aging-out GDSF its name implied. That observable behaviour is what the docstring
promises and what the ablations measured, so it must be preserved exactly.

## Decision

Replace the per-eviction linear scan with a **lazy-deletion min-heap** keyed by
the **age-invariant contribution** `freq·cost·shared_boost/size`:

- Every admission and every priority-changing hit pushes a fresh
  `(contribution, seq, key)` node; the entry records its latest `seq`. A node
  whose `seq` no longer matches the entry's (superseded by a later push) or
  whose key is gone is **stale** and skipped on pop.
- Contribution only rises (freq and the tenant set grow monotonically), so a
  key's stale nodes carry *smaller* values and are popped-then-skipped before
  its current node — the first live node popped is the true minimum-contribution
  victim, identical to the old `argmin`.
- Pinned (in-flight, ia4.4) or floor-protected current nodes are set aside and
  re-pushed after the loop, so they remain future candidates.
- Housekeeping rebuilds the heap from the live set when stale nodes dominate
  (`> 2·live + 32`), bounding heap size to O(live entries).

The per-tenant fairness floor is still computed once per pressured admission
(O(active entries)), not per victim; only the dominant O(n·k) victim scan is
removed. `self._age` continues to advance to the evicted entry's `_priority`
purely for behavioural compatibility.

## Consequences

- Victim selection drops from O(n) per eviction to ~O(log n) amortised; the
  5000-entry churn regression test that was ~O(n²) now runs in well under a
  second and keeps all 50 hot pages resident.
- Observable eviction decisions are unchanged (all pre-existing tests pass
  untouched); this is a pure performance fix, safe to measure on.
- Residual: the fairness-floor pass is still O(active) per pressured admission
  when `tenant_floor > 0`. A per-tenant incremental floor is a P3 follow-up if
  profiling shows it; the default `tenant_floor = 0` path is pure O(log n).
