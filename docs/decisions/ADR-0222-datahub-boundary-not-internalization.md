# ADR-0222: DataHub is a boundary serialization target, not SEOCHO's internal model

- Status: accepted
- Date: 2026-08-17
- Tickets: seocho-v6w (epic: DataHub interchange review loop; children .1–.8)
- Related: ADR-0121 (glossary connector Phase A), ADR-0129 (Phase B/C),
  ADR-0150 (connector materialization layer), `docs/PLUGIN_SURFACE.md`

## Context

The product goal is a non-developer review loop: `pip install seocho[datahub]`,
stand up the DataHub UI, interchange an existing SEOCHO ontology with DataHub,
have non-developers review proposed terms there, and feed approvals back into
SEOCHO to update the governed ontology. The stated motive: bespoke review
surfaces face adoption resistance and SEOCHO has no adequate UI of its own, so
"adopt a lot of the DataHub metadata scheme" was on the table — up to and
including internalizing DataHub's model (entities / aspects / URNs /
entity-registry) into SEOCHO's core.

A 12-agent review (3 ecosystem research, 3 repo-grounded reviews, 6 adversarial
verifications — all 6 CONFIRMED) evaluated that question. External evidence:
Databricks attached Unity Catalog as a boundary control-plane service and
mounted the legacy Hive metastore as one *foreign catalog* inside UC's
namespace rather than rewriting either model into the other; every healthy
DataHub integration (dbt, Airflow, Great Expectations, Feast) is a boundary
adapter, and no integrator internalizes PDL/aspects; Amundsen internalized the
Atlas model and both went stale together. Repo evidence: SEOCHO's ontology is
an atomic, whole-document artifact — `schema_fingerprint()` is version
identity, `SnapshotConflict` guards immutability, `validate()` enforces
cross-facet invariants (relationship endpoints, `broader` acyclicity) — a
git-shaped model, where DataHub's per-aspect independent UPSERTs are a
service-shaped model built for a hot multi-writer catalog.

## Decision

**Map at the boundary; do not internalize.** Specifically:

1. **Seam rule:** `urn:li:*` strings and DataHub aspect names
   (`glossaryTermInfo`, `glossaryRelatedTerms`, `structuredProperties`,
   `globalTags`, `assertion*`, …) may appear **only** in
   `src/seocho/datahub_export.py` and `src/seocho/connectors/datahub.py`.
   Everything crossing inward is normalized first: pull produces plain
   `term_records` consumed by `datahub_glossary_to_mapping_spec`; ingest
   produces `seocho.connector_record.v1` JSONL (ADR-0150).
2. **Rejected internalizations** (do not reopen without a new ADR):
   - *Aspect-style decomposition* of the Ontology document — it destroys the
     three invariants the governance stack depends on (whole-document
     fingerprint, `SnapshotConflict` immutability, cross-facet `validate()`).
     `apply_mapping_spec` already provides cheap fine-grained change.
   - *DataHub entity types as ontology citizens* — `corpuser` violates the
     standing user_id-not-in-graph decision, and catalog-infrastructure types
     in a domain ontology pollute the closed extraction vocabulary and prompt
     context.
   - *`urn:li` / entity-registry in core* — vendor-led (Acryl) model; internal
     coupling reproduces the Amundsen↔Atlas double-staleness failure.
3. **Accepted internalizations** (the "1.5"):
   - A **seocho-native URN scheme** `urn:seocho:<workspace>:<Label>:<identity-tuple>`
     (seocho-v6w.7) — not DataHub adoption but the missing *serialization* of
     the typed intern key that already exists (identity_keys + composite
     UNIQUE constraint). It makes the allocator thesis's "portable canonical
     address space" literal; DataHub `urn:li` URNs are derived from it at the
     export boundary. Requires an injective codec (the current `_slug` is not
     injective — seocho-v6w.6).
   - **Audit metadata on `OntologySnapshot`** (`created_by`, `change_source`,
     `approval_ref`; seocho-v6w.4) — borrowed from DataHub's versioned-aspect
     audit trail, landed in the JSON/log plane (never the graph), so the
     approval loop keeps its audit chain. The snapshot *model* itself stays:
     immutable-with-conflict-error is stronger than mutable-with-history for a
     guardrail artifact.
4. **Review signal = tags (`globalTags`), advisory.** Chosen by
   aspect-ownership separation: SEOCHO never writes `globalTags`, so
   re-export cannot clobber human approvals (a `parentNode` move or
   `customProperties.review_status` edit lives inside `glossaryTermInfo`,
   which SEOCHO UPSERTs — verified clobber risk). OSS DataHub has no approval
   workflow (Cloud-only), so the tag is a convention, not enforcement: the
   loop is pull → mapping-spec diff → human confirm → snapshot save with
   provenance. Approval never auto-mutates the live schema contract.
5. **DataHub is one optional surface, not SEOCHO's UI dependency.** Its
   quickstart is a multi-service stack (GMS, MySQL, search, Kafka; ~8GB) —
   requiring it for review would swap UI resistance for infrastructure
   resistance. Positioning: "if your org already runs DataHub, SEOCHO plugs
   in" (like the LangChain connector). An infra-free review path stays
   first-class (seocho-v6w.8), sharing the same mapping-spec →
   `apply_mapping_spec` → snapshot backend.
6. **Claim precision in public docs:** "DataHub integration" (glossary export
   + dataset connector). Never "DataHub-compatible"; no round-trip/approval
   claims until the live pull adapter (seocho-v6w.3) ships — per ADR-0150's
   rule requiring live service runs before compatibility claims.

## Consequences

- The differentiator stays sharp: SEOCHO = authoring/quality engine
  (scorecard, OntoClean, enforcement, canonical addressing); DataHub = a
  rendering/approval surface it exports to. Aspect-ownership separation gives
  the review loop integrity by construction (SEOCHO cannot forge approvals).
- Known pre-work surfaced by the review: structuredProperty *definitions*
  must be bootstrapped before Phase B/C live emit (GMS rejects values for
  undefined properties — live emit would fail today; seocho-v6w.1/.2);
  `OntologySnapshotStore.save()` silently overwrites evidence for a
  same-version+fingerprint re-save (seocho-v6w.5, blocks WP4).
- Hedge: index/query provenance should also emit OpenLineage events when
  lineage demand materializes, so one instrumentation serves DataHub, Atlan,
  and Marquez alike — SEOCHO is never married to one catalog.

## Validation

- 12-agent workflow (research: Unity Catalog enablement, DataHub ecosystem
  integration patterns, catalog-interface design space; reviews: model fit /
  coupling+operational risk / product positioning; 6 adversarial
  verifications, all CONFIRMED against repo code and live docs).
- Repo claims verified at file level: fingerprint-as-identity
  (`ontology_versioning.py`), `SnapshotConflict` guard + evidence-overwrite
  hole (`ontology_snapshot_store.py:156-167`), no `globalTags` writes in
  `src/seocho/`, `_slug` non-injectivity (`datahub_export.py`),
  structuredProperties validator behavior (DataHub docs).
- Docs-only change: `bash scripts/ci/check-doc-contracts.sh` before landing.
