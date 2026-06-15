# ADR-0143: Definitive Full-Corpus FinDER Profile + Selector at Full Scale

Date: 2026-06-16
Status: Proposed

## Context

The FIBO-guardrail arc (ADR-0132→0141) and the productionized builders
(ADR-0142) were all measured on samples: corpus profiles from a few hundred
FinDER references, answer evals at N=15→400. The selector that picks a
guardrail (`guardrail_selector.select_guardrail`) consumes a `corpus_profile`,
and financial runs had no canonical, full-scale one to ship as a default.

This ADR runs the corpus profile **once over the entire FinDER corpus** and
re-ranks every guardrail candidate against it, so the selector's verdict rests
on the whole distribution rather than a sample, and so a default financial
`corpus_profile` artifact exists.

## Method

`scripts/benchmarks/finder_full_corpus_profile.py`: open-extract (no fixed
schema, general type labels) every FinDER reference with ≥40 chars — **5,654
documents** — via MARA `DeepSeek-V3.1` (`provider="mara"`, key from `.env`
`ontology_guardrail_mara_api_key`), single pass, 10 workers, 429-backoff retry.
Each doc's labels are appended to a resumable JSONL (`--resume` skips done ids),
so the ~hour-long run survives interruption. The profile is the aggregate label
frequency over all docs.

Candidates scored against the full profile (`corpus_coverage` dimension,
`guardrail` weight profile):
- `curated_plus` — the hand-made `fibo_plus.jsonld` slice.
- `fibo_{BE,FBC,FND,SEC}_stable` — each official FIBO module
  (@fee10a4, snapshot a5fbf8e4), lexically bridged to the corpus
  (`bridge_to_corpus`) then semantically bridged with a **stable multi-model
  2-pass auto-derived seed** (`derive_fibo_roots_stable`, 3 MARA models:
  DeepSeek-V3.1 + MiniMax-M2.5 + gpt-oss-120b) — zero hand curation
  (ADR-0140 pipeline).

`select_guardrail` then picks the winner. Data: `ADR-0143-full-corpus.json`
(full profile + coverage table + top labels).

## Results

Full corpus: **5,654 docs, 894 distinct labels, numeric_intensity 0.4244.**

Top labels (count): FinancialMetric **38,191** (dominant), Company 10,123,
Person 6,211, Product 5,446, Risk 5,206, Regulation 3,779, Date 2,343,
FinancialInstrument 1,790, CompetitiveFactor 860, TimePeriod 788.

Coverage (corpus_coverage, full profile):

| candidate          | coverage |
|--------------------|----------|
| **fibo_FND_stable** (chosen) | **0.8759** |
| fibo_FBC_stable    | 0.8371   |
| curated_plus       | 0.7124   |
| fibo_SEC_stable    | 0.6525   |
| fibo_BE_stable     | 0.2354   |

**Selector picks `fibo_FND_stable`** — the auto-bridged official FND module
out-covers the hand-curated slice (0.876 vs 0.712) at full scale. The stable
FIBO coverages corroborate the sampled ADR-0140 numbers (FBC 0.784→0.837, FND
0.862→0.876) — the multi-model 2-pass bridge holds up over the whole corpus,
not just the sample. BE stays low (no financial roots to propagate), as before.

## Honest reading

- **Coverage is a structural/selection proxy, not answer accuracy.** ADR-0141's
  powered N=400 paired test found the stable FIBO guardrail and the curated
  slice **statistically equivalent on answers** (Δ=+0.0025, McNemar p=1.0)
  despite FIBO's coverage lead — the coverage gain did NOT carry to answers
  (coverage≠answer, ADR-0122). This ADR confirms the selector's *ranking* at
  full scale and ships a canonical *profile*; it does **not** claim FND-bridged
  beats curated on answer accuracy. The honest end state is unchanged: hand
  curation is replaceable by the automated, version-pinned FIBO pipeline at no
  measurable answer-accuracy cost, now confirmed against the entire corpus.
- **numeric_intensity 0.4244 < 0.5** (the default `numeric_threshold`): even
  though FinancialMetric is the single largest label by count, the corpus
  numeric intensity sits below the bar that would auto-force a numeric-validating
  guardrail — so numeric validation stays opt-in (ADR-0127/0131), not default,
  for this corpus. Recorded so the threshold choice is grounded in the full
  distribution, not a guess.

## Decision

Ship `ADR-0143-full-corpus.json` as the **canonical FinDER corpus profile** for
financial runs — the `corpus_profile` a `ontology.select` / `ontology.select.fibo`
run-spec block (ADR-0142) can point at by default. Keep
`finder_full_corpus_profile.py` as the reproducible harness (resumable, MARA,
self-aggregating + self-scoring).

## Validation

`scripts/benchmarks/finder_full_corpus_profile.py` ran end-to-end: 5,654 docs
extracted (JSONL), profile aggregated, 5 candidates scored, selector ran, JSON
written. No SDK code changed (the builders/selector shipped in ADR-0142);
`check-doc-contracts.sh` covers the ADR + data contract.

## Consequences

- The selector's full-corpus verdict (`fibo_FND_stable` by coverage) and a
  default financial `corpus_profile` are now first-class, reproducible artifacts.
- Closes the ADR-0142 follow-up ("a full-corpus FinDER coverage profile to ship
  as a default corpus_profile, ADR-0143, in progress").
- The FIBO arc (0132→0143) is concluded end-to-end: official upstream → catalog
  → bridge → automated stable seed → productionized selector/run-spec →
  full-corpus-grounded default profile.
