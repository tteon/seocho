# ADR-0184: publish-time compatibility gate (seocho-ia4.2)

Date: 2026-08-16 · Status: accepted · seocho-ia4.2

## Context

The compatibility classifier (ADR-0177) says HOW a new ontology version differs; it
had no enforcement point. `save()`/`register` accept any version — a BREAKING change
that invalidates all existing data can be published SILENTLY (the silent-breaking /
"strict but stale" failure). Schema registries gate this: a compatibility check before
publish.

## Decision

`ontology/publish_gate.py`:
- `check_publish_compatibility(prior, new, *, mode)` — classifies prior→new and decides
  under a schema-registry mode: **BACKWARD** (default, forbid BREAKING — old data must
  stay valid), **FORWARD** (forbid BREAKING/FORWARD), **FULL** (forbid anything
  non-BACKWARD), **NONE** (allow all). First version always allowed.
- `derive_drift_policy(report)` — the read-time drift policy the verdict implies
  (BREAKING/FORWARD → 'block', else 'warn'), tying the publish gate to the ia4.1 barrier.
- `PublishCompatibilityError` carries the report.
- `OntologySnapshotStore.publish(ontology, *, compatibility_mode="BACKWARD",
  allow_breaking=False, **save_kwargs)` — gated save: refuses an incompatible publish
  unless `allow_breaking` (an explicit, acknowledged breaking bump), then delegates to
  the (unchanged, ungated) `save()`. Returns `(snapshot, report)`.

## Consequences

- Breaking ontology publishes are now EXPLICIT and blocked-by-default, not silent. The
  same verdict derives the read-time drift policy — publish governance and the runtime
  barrier share one classifier.
- Back-compat: plain `save()` is untouched; `publish()` is the opt-in gated path.
- +4 tests. Completes ia4.2 (classifier ADR-0177 + this gate). Next lifecycle organs:
  ia4.4 EBR safe-reclamation, ia4.5 tombstone migration, ia4.3 RCU (the concurrency
  spine those two depend on).
