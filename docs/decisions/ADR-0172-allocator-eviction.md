# ADR-0172: the allocator's reclamation half — cost-aware, fair eviction

Date: 2026-08-16 · Status: accepted (design + measurement) · seocho-ia4

## Context

hadry (2026-08-16): the OS claim was incomplete — SEOCHO interns entities on
write (alloc) and admits queries (scheduling) but has NO eviction/GC/lifecycle
policy and no resource management under concurrency + heterogeneous requests. An
allocator without reclamation is not a memory manager. Confirmed status quo: the
only eviction is `OntologyContextCache` — a naive fixed-size LRU (`max_size=32`,
keyed by `id(ontology)`, no cost-awareness, no byte budget, no fairness, no
concurrency guarantee, one artifact type). Design: wiki/allocator-eviction-
lifecycle-design.md.

## Decision

`src/seocho/eviction.py::CostAwareEvictionCache` — the reclamation half of the
allocator over the hot per-request resources (compiled ontology contexts /
schema prefixes; extensible to result buffers and prefix-KV). Uses signals the
layer already has: reference **frequency**, **recompute cost** (a schema
prefix's rebuild cost is known, ADR-0166), and **size** — in a GreedyDual-Size-
Frequency value with aging, plus two agentic additions:
- **per-tenant working-set floor** (fairness): each tenant keeps its top-K most
  valuable entries; only the surplus is contended, so a churny tenant cannot
  evict another tenant's or a hot shared page.
- **shared-entry boost**: an entry referenced by many tenants is valued higher.
Keyed by a stable content hash (shareable across sessions), byte-budget (not
entry-count), thread-safe.

## Result — vs naive LRU under a multi-tenant, skewed, churny load

`scripts/agentos/eviction_bench.py`, 4000 accesses, budget ≈ half the shared
working set, deterministic (no RNG); 10 shared ontologies (Zipf, onto-0 hottest,
expensive to recompute) + a churny tenant flooding cheap one-shots.

| policy | hit-rate | recompute-ms avoided | hot-shared hit-rate (fairness) |
|---|---|---|---|
| naive LRU (status quo) | 35.3% | 56,440 | 86.6% |
| **cost-aware + fair** | **48.1%** | **76,960 (+36%)** | **99.9%** |

The cost-aware+fair policy avoids **36% more recompute cost** and holds the hot
shared ontology **99.9%** of the time under churn, vs the naive LRU letting the
churny flood evict it ~13% of the time. 5 unit tests (byte-budget eviction,
hot-expensive survival, tenant-floor fairness, shared boost, thread-safety).

## Consequences

- Completes the allocator model: alloc (interning) + **reclamation (this)** +
  scheduling (admission). "Memory manager" is now honest, not aspirational.
- CFP tracks: Memory/State/Storage (eviction/update policies, long-lived
  artifacts) + Resource/Execution (resource management under multi-agent
  contention). Turns hadry's "아직 멀었다" into a measured subsystem.
- Follow-ups (seocho-ia4): wire `CostAwareEvictionCache` in place of the naive
  `OntologyContextCache` keyed by `stable_prefix_hash`; extend to prefix-KV
  (seocho-40j) and result buffers (handle-arm); version-aware retirement + TTL;
  provenance-root GC for graph working sets; live-load measurement.
