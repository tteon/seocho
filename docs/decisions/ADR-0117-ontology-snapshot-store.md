# ADR-0117: Versioned Ontology Snapshot Store (Layer 3)

Date: 2026-06-13
Status: Proposed

## Context

The ontology-management roadmap had its measure/refine layers — scorecard
(ADR-0114), OntoClean critic (ADR-0115), purpose-weighted corpus-aware tier
(ADR-0116) — but no *persistence*. `ontology_versioning` could compute a
fingerprint and an upgrade plan between two in-memory ontologies, but there was
no store of versions, no history, and no way to answer "is v2 actually a better
guardrail than v1?" with the evidence that justified the bump. Without that,
version acceptance stays subjective and a downstream consumer cannot pull a
known-good, evidence-backed ontology by version.

## Decision

Add `seocho.ontology_snapshot_store.OntologySnapshotStore` — a filesystem-backed,
JSON, offline store of **immutable, evidence-carrying** ontology versions.

- **Content-addressed + immutable.** A snapshot is keyed by `(package_id,
  version)` and stamped with `schema_fingerprint`. Re-saving identical content is
  idempotent; re-saving a version with *different* content raises
  `SnapshotConflict` — you cannot silently mutate a published version, which
  forces a real semver bump.
- **Carries the evidence.** Each snapshot optionally stores the
  `OntologyScorecard`, the OntoClean consensus tags (`dump_metaproperties`), the
  `CorpusProfile` it was judged against, and the weight profile used.
- **Operations:** `save`, `get`, `list`/`latest` (semver-ordered), `history`
  (compact lineage timeline), and `compare(from, to)` — which reuses
  `build_ontology_upgrade_plan` + `diff_ontologies` for the schema delta and adds
  a **measured guardrail verdict**: it prefers the `corpus_coverage` delta (the
  downstream-predictive signal from ADR-0116), falling back to overall score.
- **Offline.** No DB, no LLM; it persists what the upstream evaluation produced.

This closes the measure → refine → **persist & prove** loop: a version is
accepted because its stored evidence shows it is better, and the comparison is
reproducible.

## Validation (measured 2026-06-13)

`scripts/benchmarks/ontology_versioning_demo.py` — deterministic, reuses the
FinDER corpus profile recorded by ADR-0116 (no new LLM calls). Stores the two
FinDER guardrail arms as one package's two versions and compares them. Record:
`docs/decisions/ADR-0117-versioning-snapshot-demo.json`.

| version | grade | overall (guardrail) | corpus_coverage | fingerprint |
|---|---|---|---|---|
| fibo_finder 1.0.0 (sparse) | C | 0.758 | 0.349 | 053fa00f |
| fibo_finder 2.0.0 (rich) | B | 0.804 | 0.595 | c6c9959f |

`compare(1.0.0 → 2.0.0)`: `schema_changed=True`, `recommended_bump=major`, added
`{Person, Regulation, Risk, Product, LegalIssue, Event, AccountingPolicy}`,
**guardrail verdict = "better"** (basis `corpus_coverage`, delta **+0.2464**).
The store reproduces, with persisted evidence, the conclusion the live FinDER
ablation reached — now as an objective, queryable version record.

## Consequences

- Version acceptance becomes objective and auditable; a downstream consumer can
  `get(package_id, version)` an evidence-backed ontology to use as an extraction
  guardrail, or `compare` two versions before upgrading.
- The store is the home for the refinement loop's outputs (OntoClean tags,
  corpus profiles) and the natural integration point for a future `seocho
  ontology version` CLI and a migration-script generator.
- Tests: `tests/seocho/test_ontology_snapshot_store.py` (immutability, ordering,
  history, compare verdict).
- Follow-ups (`seocho-g2r`): ABox migration-Cypher generation on bump; a
  retention/GC policy; remote/object-store backend behind the same interface.
