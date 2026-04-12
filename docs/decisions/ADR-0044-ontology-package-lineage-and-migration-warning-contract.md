# ADR-0044: Ontology Package Lineage And Migration Warning Contract

Date: 2026-04-12
Status: Accepted

## Context

The ontology governance CLI can validate, export, and diff schema files, but
plain `name + version` is not enough to manage long-lived ontology evolution.

Two gaps remained:

- ontology lineage was implicit rather than explicit
- diff output listed structural changes but did not provide semver-aware
  migration guidance

Without a stable package lineage, downstream runtime bundles, prompt contracts,
and graph migrations can drift even when ontology names change or split.

## Decision

SEOCHO will treat ontology lineage and ontology versioning as two separate
signals:

- `package_id`: stable package lineage identifier
- `version`: semver-style release marker within that lineage

The runtime ontology object now carries `package_id`, defaulting to `name` when
not explicitly set.

The governance diff path now emits:

- `recommended_bump`: `none | patch | minor | major`
- `requires_migration`: boolean
- `migration_warnings`: explicit operator-facing warnings

The warnings are conservative on purpose:

- removal or mutation of existing node/relationship definitions is treated as a
  major migration signal
- additive schema growth is treated as minor
- metadata-only changes are treated as patch-level
- package lineage changes are treated as package migration boundaries

## Consequences

Positive:

- ontology evolution becomes easier to reason about before runtime rollout
- package lineage stays stable even if display names change
- semver mistakes become visible in offline governance instead of surfacing only
  during runtime drift

Tradeoffs:

- migration guidance is intentionally conservative and may over-warn
- this is not a full graph migration planner
- operators still need downstream query and constraint validation

## Implementation Notes

- ontology metadata: `seocho/ontology.py`
- governance warnings: `seocho/ontology_governance.py`
- CLI output: `seocho/cli.py`
