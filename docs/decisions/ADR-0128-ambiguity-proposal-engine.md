# ADR-0128: Ambiguity-Review Proposal Engine (Phase 2)

Date: 2026-06-14
Status: Proposed

## Context

The ambiguity-review loop (seocho-2mg) Phase 1 (PR #309) quarantines OOV mentions,
clusters them, and applies a declarative mapping-spec to the taxonomy. Its
proposals were heuristic (`starter_mapping_spec`). Phase 2 generates *LLM*
proposals, ranked by their measured effect, so the human reviews a
consequence-annotated list rather than guessing.

## Decision

Add to `seocho.ontology_ambiguity`:

- `MappingProposal` (surface, action, target, parent, description, confidence,
  predicted_coverage_delta, rationale) with `to_spec_entry()` / `to_dict()`.
- `propose_mappings(clusters, ontology, *, backend, model=None, top_k=20)` — asks
  the LLM (injected `backend`, via the provider-aware structured layer ADR-0120)
  to choose `alias` / `new_class` (+parent, +definition) / `ignore` for each top
  cluster. Each non-ignore proposal is then **scored offline**: apply it with
  `apply_mapping_spec`, re-score `corpus_coverage` against a profile built from
  the clusters, and record `predicted_coverage_delta`. Proposals are ranked by
  predicted lift then confidence.
- `proposals_to_mapping_spec(proposals, *, min_confidence)` — converts accepted
  proposals into a mapping-spec consumable by `apply_mapping_spec`.

The LLM call is injected → fake-testable; scoring/conversion is pure/offline.

## Validation

`tests/seocho/test_ambiguity_proposals.py` (3, fake backend): proposals parse and
a `new_class` for a high-frequency cluster gets a positive predicted coverage
delta and ranks first; `proposals_to_mapping_spec` filters by confidence, drops
`ignore`, and round-trips through `apply_mapping_spec` (adds the class under its
parent); empty clusters → empty. `run_basic_ci` green.

## Consequences

- The review queue now shows, per ambiguous surface, the recommended action +
  confidence + the measured coverage lift of accepting it — the
  consequence-preview the design called for, headless and reproducible.
- Pairs with Phase 1 (quarantine/apply) and the DataHub surface (seocho-qxj) which
  will render these proposals for human approval.
- Follow-ups: OntoClean pre-validation of proposed is-a placements; batch live
  run over the FinDER quarantine; confidence calibration against accepted/rejected
  outcomes.
