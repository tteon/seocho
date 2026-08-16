# ADR-0188: RCU active-version pointer + atomic CAS (seocho-ia4.3 B1)

Date: 2026-08-16 · Status: accepted · seocho-ia4.3 (B1 of the RCU build)

## Context

The RCU model for versioned ontologies (design: wiki/rcu-ontology-versioning-design.md,
revised after a 2-reviewer adversarial pass that found 3 blockers) needs ONE mutable
word per (workspace, package_id): the active-version pointer, swapped by an atomic CAS.
B1 is that pointer + CAS only (B2 pin, B3 EBR gate follow).

## Decision

`ontology/active_pointer.py::ActiveOntologyPointer` — SQLite-backed, cross-process
atomic (`BEGIN IMMEDIATE` + conditional `UPDATE ... WHERE generation=? AND epoch=?`),
per (workspace_id, package_id):
- **Real CAS, not TOCTOU** (review fix): the swap is a single atomic conditional write
  on the read (generation, epoch) — NOT select-then-compare. Concurrent publishers with
  the same `expected` → exactly one wins (epoch bumps once), losers see stale expected
  and no-op.
- **(generation, epoch), globally non-decreasing** (review fix #7): epoch per swap;
  generation bumps on recreate, seeded from a persistent `generation_hwm` table that
  survives delete/restore → a stale reader's old epoch can never be reused against a new
  version.
- **Fencing token** rejects a returned-from-the-dead leader below the stored token; the
  `expected` CAS is the linearization point.
- Workspace-keyed (the snapshot store lacked a workspace dimension); this is the
  "active" pointer the store lacked (`latest()`-by-sort ≠ active).

## Result

8 tests: first-publish + read, can't first-publish over an existing pointer, CAS swap
bumps epoch, wrong-expected fails, stale-fencing-token rejected, **N concurrent CAS →
exactly one winner (epoch bumps once)**, **recreate → generation strictly increases**,
workspace/package isolation.

## Consequences

- The RCU read/publish protocol's write side is in place and linearizable. Next: B2
  (increment-then-recheck reader pin in a REQUEST-level context, decoupled from
  admission), B3 (EBR gate + conservative shared-store retention). SQLite backing now;
  etcd-compatible interface later. seocho-ia4.3.
