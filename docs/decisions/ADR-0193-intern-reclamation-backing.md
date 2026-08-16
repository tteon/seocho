# ADR-0193: SharedInternTable — free()/reclamation + cross-process backing

- Status: accepted
- Date: 2026-08-16
- Tickets: seocho-ia4 (review-hardening)
- Related: ADR-0183 (cross-model intern), ADR-0160/0161/0162 (interning)

## Context

The 4-reviewer panel named the intern table the paper's *wedge* (a portable
canonical address space) and, in the same breath, its two sharpest defects:

1. **"A heap with no `free()`."** The table only ever grew; a long-lived process
   (a cross-session persisted namespace, a fleet worker) accumulates entries
   without bound → OOM.
2. **Process-local, with a racy JSON merge.** Two processes sharing a namespace
   via `persist`/`load` could interleave and lose writes; there was no
   cross-process first-writer-wins, so the "one canonical address per entity"
   guarantee held only within a single process.

## Decision

**Reclamation (the missing `free()`), bounded and safe.** Add an optional
`max_entries` cap and a reference count:

- `retain` / `release` (and a `referenced` context manager) count the live
  consumers of a canonical address; `reclaim()` / the on-insert pass evict
  **LRU zero-reference** entries once over cap. A referenced (in-flight) address
  is never reclaimed under its holder.
- Reclamation is correctness-preserving because the canonical id is a
  deterministic function of identity (`index/identity.py`): a reclaimed entry
  re-interns to the *same* address. Reclamation is thus a cache miss, not a
  semantic change. Default `max_entries=None` keeps the unbounded, backward-
  compatible behaviour; long-lived callers opt in.

**Cross-process shared namespace (optional SQLite backing).** With
`sqlite_path` set, `intern` performs a cross-process **atomic first-writer-wins**
(`INSERT OR IGNORE` + `SELECT`) into a durable table; the sharded in-memory maps
become a bounded, *coherent-by-construction* cache — a `(workspace, identity)`
mapping is written once and never mutated, so a cached value can never be stale
and L1 reclamation is always safe. This replaces the racy JSON merge for the
read/intern path.

**Atomic persist.** The legacy JSON snapshot now writes a temp file and
`os.replace`s it, so a concurrent reader never sees a torn file.

## Consequences

- The wedge is no longer "a heap with no free()": RAM is bounded on demand and
  the shared namespace is genuinely cross-process under the SQLite backing.
- Deliberately **out of scope** (deferred, per the panel): cross-process
  *refcount-driven* reclamation and a distributed (Redis/etcd) backing. The
  durable SQLite store is append-only and GC'd offline; only the per-process L1
  is bounded here. This is honest about what "fleet-distributed" would still
  require, without over-building a kernel ahead of demand.
- Indexing behaviour is unchanged (the pipeline's table keeps the unbounded
  default); the mechanism is available where a long-lived table needs it.
