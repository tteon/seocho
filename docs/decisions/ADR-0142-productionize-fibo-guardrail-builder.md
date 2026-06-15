# ADR-0142: Productionize the FIBO-Derived Guardrail Builder (Selector + Run-Spec)

Date: 2026-06-16
Status: Proposed

## Context

The FIBO arc (ADR-0132→0141) established that a fully-automated, version-pinned
FIBO-derived guardrail equals the hand-curated slice on answers and exceeds it on
coverage. Those pieces lived only in benchmark scripts. This wires them into the
SDK + run-spec as first-class builders so a run can use a FIBO-derived guardrail
declaratively.

## Decision

`seocho.fibo_catalog`:
- `build_fibo_guardrail(catalog, corpus_profile, module, *, backends=None, models=None,
  collapse=False, fallback_seed=None)` — corpus-bridge one FIBO module (lexical +
  semantic). `backends` → stable multi-model auto seed (ADR-0140); else
  `fallback_seed`/`FINDER_FIBO_ROOTS` (offline). `collapse=True` → small generic
  guardrail (version-pinned).
- `select_fibo_guardrail(catalog, corpus_profile, *, modules=None, backends=None,
  models=None, collapse=True, extra_candidates=None)` — build bridged module
  candidates + pick the best via `guardrail_selector.select_guardrail`; returns
  `(recommendation, {name: Ontology})`.

Run-spec (`ontology.select.fibo`): `{catalog, modules?, bridge: stable|lexical,
derive_models?}` + `corpus_profile`. `e2e.resolve_guardrail` builds the bridged
candidates and selects; the chosen guardrail is held in memory
(`spec.resolved_ontology`, no file), and `build()` uses it directly. `bridge:
stable` derives the seed via the MARA provider; `lexical` is offline.
`RUN_SPECS.md` documents the block.

## Validation

`tests/seocho/test_fibo_catalog.py` (+2): `build_fibo_guardrail` collapse + version
pin; `select_fibo_guardrail` picks the corpus-relevant FIBO module over an
irrelevant curated candidate (offline fallback seed).
`tests/seocho/test_run_spec_guardrail_select.py` (+3): parse `select.fibo`; require
candidates-or-fibo; `resolve_guardrail` (offline lexical) builds an in-memory
FIBO-derived guardrail and selects it. `run_basic_ci` + `check-doc-contracts` pass.

## Consequences

- A run can declare an official, version-pinned FIBO-derived guardrail in YAML —
  no hand-made slice, no Python. The selector picks the best-covering module,
  bridged to the run's corpus.
- `bridge: stable` spends a few MARA derive calls at resolve time; `bridge: lexical`
  is free/offline. Default `stable`.
- Follow-up: a full-corpus FinDER coverage profile to ship as a default
  `corpus_profile` for financial runs (ADR-0143, in progress).
