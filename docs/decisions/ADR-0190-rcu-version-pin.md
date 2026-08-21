# ADR-0190: RCU reader pin/epoch registry (seocho-ia4.3 B2)

Date: 2026-08-16 · Status: accepted · seocho-ia4.3 (B2)

## Context

B2 = the read side of RCU: a request pins the ACTIVE version's epoch for its whole
duration (no torn read); min-pinned-epoch tells the reclaimer which versions still have
readers. The B1/B2 adversarial review found all three blockers here; this incorporates
the fixes.

## Decision

`ontology/version_pin.py::VersionPinRegistry(pointer)` — sharded refcount by
(ws, pkg, epoch), composing with the B1 ActiveOntologyPointer:
- **[fix #1] increment-then-recheck** (publish-before-observe): `pin` reads the pointer →
  E, increments refcount[E], RE-READS; if the pointer advanced, decrements E and retries
  on the new epoch. The refcount is published before the reader proceeds, and the
  returned epoch is refcounted AND current — closing the read-vs-reclaim race. `pin`
  returns the epoch it incremented; `unpin` decrements THAT epoch (never a fresh read).
- **[fix #2] request-level, decoupled from admission**: a standalone registry a
  request-context wrapper drives (`with reg.pinned(ws,pkg): ...` around the whole
  request), NOT wired inside LaneScheduler.acquire (gate-disable short-circuit + per-
  Cypher-call granularity would both break the guarantee).
- **[fix #6] `min_pinned_epoch` returns None with no pins** (never "current") — the B3
  gate owns the grace-period decision.
- **[fix #4] liveness** = the `pinned` context manager releases in `finally` (also on
  request abort/deadline), never an external forced-unpin.

## Result

7 tests: pin returns+refcounts current epoch, None when no pointer, tracks a new epoch
after a swap, **increment-then-recheck retries a mid-pin swap (transient epoch released,
stable epoch pinned)**, min-pinned reflects the oldest live pin and advances as readers
leave, context-manager release, 16 concurrent pins with no leak.

## Consequences

- The RCU read side is correct per review. Next: B3 (EBR reclamation gate = mark
  RETIRING-first-then-read-refcounts + grace-period quiescence + conservative shared-
  store retention; = ia4.4-full + ia4.5-vacuum), then B4 (barrier uses pinned_epoch).
  seocho-ia4.3.
