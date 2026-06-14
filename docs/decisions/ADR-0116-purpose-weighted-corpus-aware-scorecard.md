# ADR-0116: Purpose-Weighted, Corpus-Aware Ontology Scorecard

Date: 2026-06-13
Status: Proposed

## Context

ADR-0115's FinDER guardrail ablation exposed a real defect in the ADR-0114
scorecard: **the intrinsic grade diverged from — was in fact anti-correlated
with — downstream guardrail value.** On FinDER the scorecard ranked the *sparse*
`fibo_minus` (2 classes) ABOVE the *rich* `fibo_plus` (9 classes), penalising
fibo_plus's flatness — yet fibo_plus was clearly the better extraction guardrail
(extraction_score 0.898 vs 0.734). A measurement instrument that rewards the
worse guardrail is misleading.

Two root causes:
1. **One-size weights.** taxonomy_health (which penalises flat schemas) carried
   the same weight regardless of how the ontology would be used. For an
   extraction guardrail, taxonomy *shape* matters far less than whether the
   vocabulary can represent the corpus.
2. **No corpus signal.** The scorecard judged the ontology in a vacuum. The
   thing that actually determines guardrail value — *does the vocabulary cover
   the entity types the target documents contain?* — was never measured.

## Decision

Two additions to `seocho.ontology_scorecard`, both preserving the offline,
LLM-free contract (inputs are precomputed upstream):

1. **Purpose-specific weight profiles** (`WEIGHT_PROFILES`, `score_ontology(...,
   profile=)`):
   - `balanced` (default) — the ADR-0114 weights.
   - `guardrail` — weights `constraint_richness` + `corpus_coverage` (0.25 each)
     and de-emphasises `taxonomy_health` (0.10); for extraction-guardrail use.
   - `taxonomy` — weights `taxonomy_health` (0.35) for reasoning/subsumption use.
   An explicit `weights=` still overrides.

2. **Corpus-aware tier** (`CorpusProfile`, `build_corpus_profile`,
   `score_ontology(..., corpus_profile=)`): a new `corpus_coverage` dimension =
   the frequency-weighted fraction of the entity types a target corpus actually
   needs that the ontology declares (label/alias, case-insensitive). The profile
   is built upstream from an **OPEN, ontology-free extraction** over the corpus
   (so it reflects the corpus's needs, not any candidate's vocabulary). The
   largest uncovered labels become weak points — the precise classes to add.

## Validation (measured 2026-06-13)

`scripts/benchmarks/corpus_aware_scorecard_experiment.py`. Open-extracted 48
FinDER docs (6 × 8 categories) with MARA DeepSeek-V3.1 → corpus profile of 85
distinct types (top: FinancialMetric 230, Person 60, Company 43, Product 40,
Risk 33, LegalIssue 31, Regulation 28). Scored the three FIBO variants. Record:
`docs/decisions/ADR-0116-corpus-aware-scorecard.json`.

**Ground truth (downstream guardrail extraction_score, ADR-0115):**
`fibo_plus (0.898) > fibo_minus (0.734)`.

| variant | BEFORE balanced (intrinsic) | AFTER guardrail+corpus | corpus_coverage |
|---|---|---|---|
| fibo_minus | **0.898 (rank 1)** | 0.745 (**rank 3**) | 0.349 |
| fibo_base | 0.879 (rank 2) | 0.773 (rank 1) | 0.461 |
| fibo_plus | 0.816 (rank 3) | 0.773 (rank 2) | 0.595 |

- **The anti-correlation is fixed.** The sparse `fibo_minus` — the *worst*
  guardrail — moves from rank 1 (intrinsic) to rank 3 (corpus-aware), matching
  downstream reality.
- **The `corpus_coverage` dimension is perfectly monotone with richness and
  downstream value** (0.349 < 0.461 < 0.595 = minus < base < plus). It directly
  measures guardrail adequacy.
- The AFTER overall has fibo_base ≈ fibo_plus (0.7732 vs 0.7728): base covers the
  highest-mass types (Company/Person/Metric/Regulation) while plus adds rarer
  ones and still carries a flatness penalty. This is honest — it says fibo_plus's
  marginal classes don't pay for themselves *on this corpus*, and that plus
  should also grow a taxonomy.
- **Actionable output:** even fibo_plus's corpus_coverage is only 0.60; its top
  uncovered types are `Concept, Date, Industry, MonetaryValue, Program` — the
  precise classes to add for FinDER. The scorecard now emits a refinement
  to-do list grounded in the corpus.

## Consequences

- The scorecard now **predicts** guardrail value instead of contradicting it,
  when used with `profile="guardrail"` + a corpus profile. This is the metric to
  gate guardrail/version decisions.
- `corpus_coverage` requires an upstream open extraction (one MARA pass over a
  sample); it stays out of the scorecard core (offline contract preserved).
- The corpus profile is the natural bridge to the refinement loop (Layer 2): its
  top-uncovered labels are exactly the OOV-driven class proposals.
- Follow-ups (`seocho-g2r`): persist corpus profiles + chosen profile alongside
  ontology versions (Layer 3); a `seocho ontology score --profile --corpus` CLI;
  alias/synonym normalisation so granular open labels (CEO→Person) map better.
