# ADR-0133: Compiled FIBO Catalog → Guardrail-Candidate Ontologies

Date: 2026-06-14
Status: Proposed

## Context

ADR-0132 vendors official FIBO as a pinned submodule and compiles it into
`catalog.json` (`seocho.fibo_catalog.v1`: per-module label/definition/IRI
indexes). Meanwhile the guardrail selector (ADR-0123) and scorecard (ADR-0114)
were scoring **hand-made** candidates (`examples/datasets/fibo_{minus,base,plus}
.jsonld`). The authoritative, version-pinned catalog should feed them instead.

## Decision

Add `seocho.fibo_catalog` (pure/offline; consumes the compiled JSON, never raw
OWL — ADR-0132 boundary):

- `catalog_module_to_ontology(catalog, module_code)` — one `Ontology` per FIBO
  module: classes → nodes (IRI local-name as label, human label as alias,
  definition, `same_as` = IRI, `broader` from subClassOf within the module);
  object/datatype properties → relationships (domain/range mapped to class
  labels). `package_id = fibo.<module>`, `version = <fibo_commit>` so the choice
  is version-pinned.
- `fibo_guardrail_candidates(catalog, modules=None)` → `{module: Ontology}` map,
  fed directly to `guardrail_selector.select_guardrail`.
- `catalog_provenance(catalog)` → `{schema_version, fibo_commit, snapshot_hash}`
  to attach to a snapshot/run (composes with `OntologySnapshotStore`, ADR-0117).

## Validation

`tests/seocho/test_fibo_catalog.py` (4): catalog schema validation; module →
ontology builds nodes/relationships, maps subClassOf to `broader`, human label →
alias, `same_as`/`package_id`/`version` (pinned to the FIBO commit); provenance;
candidates feed `select_guardrail` and score corpus_coverage > 0. Built against a
synthetic `seocho.fibo_catalog.v1` fixture (no submodule/compiler run needed).
`run_basic_ci` green.

## Consequences

- The guardrail selector / scorecard can now score the **official, version-pinned
  FIBO** modules as candidates, not hand-made slices — closing the loop between
  ADR-0132's upstream pipeline and the ontology-management stack.
- A guardrail choice carries the FIBO commit + snapshot hash → reproducible,
  citable runs.
- Follow-ups: run the real compiler (`git submodule update --init` +
  `compile_fibo_snapshot.py`) and re-run the FinDER guardrail/answer experiments
  against the pinned catalog; record `catalog_provenance` in snapshot store
  provenance; wire `examples/finder/datasets/fibo_modules` curated slices vs the
  catalog compatibility report into the scorecard.
