# ADR-0196: Freshness — real read-time repair (not a serve stub)

- Status: accepted
- Date: 2026-08-16
- Tickets: seocho-ia4.6 (under epic seocho-ia4)
- Related: ADR-0175 (drift barrier), ADR-0176 (freshness policy),
  ADR-0186 (soft-delete migration, ia4.5)

## Context

The bounded-staleness freshness policy (ADR-0176) classifies a drifted read as
serve / repair / refuse. But `freshness_to_drift_policy` mapped **repair → warn**
and nothing acted on it: the review flagged "repair" as *stubbed to serve* — the
read proceeded against data indexed under an older contract with no
reconciliation, which is precisely the staleness the barrier exists to stop.

Separately, the soft-delete migration (ia4.5) marks logically-removed data with
`_ontology_soft_deleted_at` instead of destroying it — but nothing filtered
those rows out of reads, so removed data could still surface in answers.

## Decision

Make "repair" a real **read-time reconciliation** and wire it into the query
path:

- `repair_read(records, *, deprecated_properties, reconcilable)` reconciles
  retrieved records to the ACTIVE contract: it drops any record referencing a
  soft-deleted node and strips deprecated property values. If the change is a
  non-repairable break (`reconcilable=False`), it returns the records untouched
  and reports it so the caller escalates to refuse.
- `plan_read_repair(migration_plan)` derives the reconcilable part (deprecated
  properties, removed labels, and whether the change is read-repairable at all)
  **off the hot path**, from `Ontology.migration_plan` output.
- `local_engine` calls `repair_read` right after the drift barrier: on a
  proceeding drift (mismatch but not blocked), records are reconciled before the
  answer is built, and the repair report is attached to the mismatch assessment.

Crucially this respects the "keep ontology reasoning out of hot request paths"
guardrail: the hot-path repair is an O(records) scan of self-describing data
(the soft-delete stamp, a small deprecated-property set); the ontology-diffing
that produces the deprecated-property set is the cold-path `plan_read_repair`.

## Consequences

- "repair" now reconciles instead of silently serving stale data; and the
  soft-delete read-leak is closed on the drift path.
- Zero behaviour change when there is no drift (records pass through untouched);
  the reconciliation triggers only on a detected, proceeding mismatch. All
  drift-barrier / drift-policy / ontology-context tests pass unchanged.
- A destructive (hard-delete) migration is correctly marked non-read-repairable
  → such a drifted read refuses rather than pretending to reconcile.
