# ADR-0129: DataHub Connector Phase B/C — Ambiguity Proposals + Governance as Structured Properties / Assertions

Date: 2026-06-14
Status: Proposed

## Context

ADR-0121 (Phase A) exports an ontology to a DataHub Business Glossary. The user
chose DataHub as the ambiguity-mapping/distribution surface. Phase B/C surface the
rest of the SEOCHO loop in DataHub: the ambiguity-review queue, and the governance
signals (scorecard, numeric validation) DataHub itself can't compute.

## Decision

Extend `seocho.datahub_export` (pure dict-MCP construction, no live `datahub`):

- **Phase B** — `ambiguity_clusters_to_glossary_proposals(clusters, *,
  package_id, status="PROPOSED")`: each cluster becomes a PROPOSED `glossaryTerm`
  under a `<package_id>.Proposed` node, customProperties carrying frequency,
  signals, candidate_labels, review_status. The review queue, visible in DataHub.
- **Phase C** — `scorecard_to_structured_properties(scorecard, *, target_urn)`:
  overall_score / grade / blocking / each dimension score as `structuredProperties`
  under `seocho.scorecard.*` on the package node. And
  `numeric_validation_to_assertions(validation, *, dataset_urn,
  confidence_threshold)`: an `assertionInfo` + `assertionRunEvent` (SUCCESS iff
  confidence ≥ threshold and no warnings) — numeric validation (ADR-0127) as a
  DataHub data-quality assertion.

Deterministic URNs; no `datahub` dependency in these functions. Aspect field
names follow DataHub's documented model and must be verified against the target
`datahub` version before live emit.

## Validation

`tests/seocho/test_datahub_bc.py` (3): clusters → N PROPOSED terms + 1 Proposed
node with deterministic URNs and review_status/frequency in customProperties;
scorecard → structuredProperties MCP carrying overall/grade/blocking + each
dimension; numeric validation → assertionInfo+assertionRunEvent with SUCCESS for a
clean result and FAILURE for one with a warn finding. `run_basic_ci` green.

## Consequences

- The full SEOCHO loop is now expressible in DataHub: governed glossary (A),
  review queue (B), and quality/quality-gate signals (C) — SEOCHO computes them,
  DataHub renders/approves them.
- Follow-ups (seocho-qxj): wire these into `emit_to_datahub` paths + a CLI; verify
  exact aspect shapes against a live GMS; round-trip approvals (DataHub term
  status → mapping-spec) to close the loop with `apply_mapping_spec`.
