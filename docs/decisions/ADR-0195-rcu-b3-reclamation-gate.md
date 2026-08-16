# ADR-0195: RCU B3 — EBR safe-reclamation gate for retired ontology versions

- Status: accepted
- Date: 2026-08-16
- Tickets: seocho-ia4.4 (under epic seocho-ia4)
- Related: ADR-0188 (RCU B1 active pointer), ADR-0190 (RCU B2 reader pins),
  ADR-0117 (immutable snapshot store)

## Context

B1 (`ActiveOntologyPointer`) swaps the active version by atomic CAS; B2
(`VersionPinRegistry`) has each request pin the active epoch and publishes
`min_pinned_epoch`. The 4-reviewer panel flagged the gap between them bluntly:
**"the RCU pins gate nothing."** Nothing consumed `min_pinned_epoch` to actually
free a retired version, so the immutable snapshot store grew without bound
(every published version kept forever), and any ad-hoc delete would race a reader
mid-request (use-after-free). B3 — the reclaimer — was the missing third piece.

## Decision

Add `SafeReclamationGate` (`ontology/reclamation.py`), the epoch-based
reclamation (EBR) gate, plus `OntologySnapshotStore.delete` (the store's only
mutation, reserved for this gate).

- On a pointer swap, the caller records the outgoing version's **retirement
  epoch** = the new active epoch (`gate.retire(...)`), in a small cross-process
  SQLite table.
- `gate.reclaim(ws, pkg)` frees a retired version iff **no reader can still
  reach it**: `min_pinned_epoch is None` (no readers) OR
  `min_pinned_epoch >= retirement_epoch`. On reclaim it deletes the immutable
  snapshot and forgets the retirement record; `dry_run` reports the decision
  without mutating.

The rule is **conservatively safe by construction** — it never frees a version a
live reader might dereference. A newer reader (pinned at or above the retirement
epoch) does not gate an older version, so the grace period is epoch-precise, not
a blanket "any reader blocks everything." Across a pointer recreation
(generation bump / epoch reset) it may only *delay* reclamation to a later pass,
which is the correct bias for a reclaimer.

## Consequences

- Pins now gate something real: `test_reclamation.py` demonstrates a pinned
  reader holding a retired version resident until it leaves, and a newer reader
  correctly not holding an older version. The claim "SEOCHO keeps versioned
  ontologies correct AND bounded under mutation" is now backed by code, not
  asserted.
- Snapshot-store growth is bounded by reclaiming drained retired versions; the
  active version and any reader-reachable version are never touched.
- Deliberately out of scope (deferred): a background reclaimer thread /
  scheduler cadence, and cross-generation epoch reconciliation — the gate is
  invoked explicitly and errs toward holding. Exported from `seocho.ontology`.
