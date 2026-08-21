# ADR-0186: memory-manager demonstration + tombstone migration (seocho-ia4.4/ia4.5)

Date: 2026-08-16 · Status: accepted (demonstrated + implemented) · seocho-ia4

## Part A — memory-manager demonstration (ia4, hadry's scenario)

hadry's mental model: an ontology has a ref-count (freq + pins); it is looked up in
the intern hash table and used; when memory fills, the memory manager (cost-aware +
fair + pin-safe eviction) decides what to evict and what to protect. This is exactly
the shipped pieces (CostAwareEvictionCache = manager, SharedInternTable = hash table,
pin refcount = ia4.4). `scripts/agentos/demo_memory_manager.py` runs the scenario
end-to-end and asserts four invariants — ALL HOLD:

- **pinned_in_use_never_evicted** — a request pins its (deliberately lowest-value)
  ontology; a churny tenant floods; the pinned in-use ontology is never evicted.
- **hot_shared_retained_under_churn** — a hot ontology shared by many tenants stays
  resident under churn (fairness floor + shared boost).
- **cold_reclaimed_when_unpinned** — once the request unpins, that cold ontology
  becomes an eviction candidate again and is reclaimed.
- **memory_manager_evicted_under_pressure** — 64 evictions under a byte budget of
  ~4 contexts vs an ~8+ working set; final hit-rate 49%, recompute-ms avoided 5200.

The memory manager works as hadry described: value ranking + pin-safety + per-tenant
fairness, under real memory pressure.

## Part B — tombstone-not-delete migration (ia4.5)

`Ontology.migration_plan(new, *, tombstone=True)` — a removed label/relationship is
now **tombstoned** (`SET n._ontology_tombstoned_at=<version>`, hidden from new reads),
not `DETACH DELETE`d; a removed property is kept and marked `_deprecated_<prop>` rather
than dropped. Physical deletion becomes a later GC decision on a retention clock (gated
by the RCU epoch, ia4.3) — not a destructive migration side effect. Every statement
carries a `data_loss` flag. `tombstone=False` restores the legacy destructive plan.

## Consequences

- The allocator's memory-manager story is demonstrated, not just unit-tested — the
  paper's "OS memory manager" claim has an end-to-end scenario behind it.
- Migration is non-destructive by default (Iceberg/Delta VACUUM discipline: evolve
  never destroys; a retention clock does). +3 tests (tombstone default + destructive
  opt-in). The epoch-gated vacuum + RELABEL/BACKFILL + the lazy/eager scavenger remain
  ia4.5/ia4.3 follow-ups.
