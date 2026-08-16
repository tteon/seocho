# ADR-0185: pin-aware eviction — safe-reclamation gate (light) (seocho-ia4.4)

Date: 2026-08-16 · Status: accepted · seocho-ia4.4

## Context

The cost-aware eviction cache (ADR-0180) ranks by VALUE (GDSF) but had NO safety
gate: it could evict an entry a concurrent request is mid-flight on (a
use-after-evict / use-after-free-class latent bug). Safety (never reclaim something
in use) is orthogonal to value (what's least useful).

## Decision

Add a pin refcount to `CostAwareEvictionCache`:
- `_Entry.pins`; `pin(key)` / `unpin(key)` / `pinned(key)` context manager /
  `pinned_count()`; `stats()["pinned"]`.
- `_evict_to_budget` chooses victims only among **unpinned** (and unprotected)
  entries — a pinned entry is never evicted, even if it is the lowest-value victim.
  If everything over budget is pinned/protected, the cache stays temporarily over
  budget until an unpin, rather than reclaiming an in-use entry.

This is the **light** safe-reclamation gate: it closes the use-after-evict bug on the
hot cache with a simple refcount, RCU-free. The **full** epoch-based gate (version
reclamation via `reader_refcount(epoch)==0 AND epoch < min_pinned_epoch AND
provenance_refcount==0 AND not ACTIVE`) needs the RCU reader-pin/epoch clock and lands
with ia4.3.

## Consequences

- The eviction cache is now cost-aware AND safe: value ranks only among already-safe
  candidates. Callers pin an entry for the duration of an in-flight use
  (`with cache.pinned(key): ...`).
- +2 tests (pinned entry survives pressure; context manager releases). Completes the
  light half of ia4.4; the epoch-gated version-reclamation half is ia4.3-dependent.
