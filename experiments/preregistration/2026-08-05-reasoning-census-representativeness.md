# Pre-registration — the reasoning census (s4/an3) and the
# representativeness check (an4)

Written and committed before any extraction under tag s4 and before any an3
or an4 call. Prefixes **RS-** (reasoning census) and **RP-**
(representativeness).

## Why these two runs exist

FinDER annotates its own questions along two axes: `reasoning` (does the
question require it) and `type` (which arithmetic it needs). Those axes
partition the corpus into three strata, and after s1/s2 (simple, 280 of
4,616) and s3 (arithmetic, 140 of 883) one stratum remains untouched:
**reasoning=True, type=None — 204 cases**, 194 of them Company overview,
all single-reference. These are the narrative-composition questions closest
to the original table's S3/S4 intuition, minus the arithmetic. Covering it
completes the annotation grid, so every claim of the form "graph versus
text" can be reported per stratum with nothing annotated left out.

Separately, both existing samples were drawn with deliberate bias (category
balance; arithmetic stratification), so no result yet speaks for the
corpus's own mix. The representativeness check does only that, at the
cheapest possible price.

## RS — the reasoning census (tag s4, answering tag an3)

- **Cases:** all 204 — a census, not a sample. No draw, no seed, nothing to
  dispute. File: `dataset/reasoning_census_cases.txt`, committed here.
  169/204 (83%) carry figures in the gold answer; the number-overlap
  primary applies there, and the 35 prose-gold cases fall to the judge
  panel and token-F1 sensitivity, reported apart as everywhere else.
- **Extraction:** conditions A and C, three models, same everything as
  s1/s2/s3. 204 × 2 × 3 = 1,224 extractions, single-reference (~2 calls
  each). deepseek's share is staged on its own quota days.
- **Answering:** the an1 stack unchanged, graphs served from s4
  (`--graph-a-tag s4 --graph-c-tag s4`), judge draw 30 by seed 42.
  204 × 5 × 3 = 3,060 calls.

Hypotheses:

- **RS-H1** · the gate holds: passages beat closed book. *Disconfirmed if*
  not separated; downstream rows then void.
- **RS-H2** · passages ≥ both graph conditions on number overlap. This
  stratum has no §19 pre-registered graph-favorable direction (S3/S4 were
  defined on `type` and multi-reference respectively, not on this
  annotation), so the registered direction is the repository's thrice-
  measured prior. *Disconfirmed if* a graph condition beats passages —
  which, on the stratum annotated as requiring composition, would be the
  result most worth having.
- **RS-EC1..3** · the evidence-conditional decomposition replicates as in
  the an2 addendum: grounded-correct reversal on ≥2 models (EC1),
  contamination asymmetry passages > graphs (EC2), deepseek over-refusal
  concentrated on graph conditions (EC3).

## RP — the representativeness check (answering tag an4, no extraction)

- **Cases:** 200 drawn by `random.Random("42-representative")` from the
  4,336 unused simple-stratum cases with references. File:
  `dataset/representative_sample_cases.txt`, committed here. The draw's
  category mix (Footnotes 45 … Financials 13) is the corpus's own, which
  is the point.
- **Conditions:** closed_book and passages ONLY — no graphs, so no
  extraction, no new Part 1 claims. This checks whether the balanced
  sample's gate and contamination findings transfer to the natural mix.
  200 × 2 × 3 = 1,200 calls.

Hypotheses:

- **RP-H1** · the gate replicates: passages beat closed book, separated,
  per model. *Disconfirmed if* any model's interval crosses zero.
- **RP-H2** · the balanced sample did not distort the gate's size: each
  model's paired passages−closed_book difference on this sample falls
  inside (or overlaps) its an1 bootstrap interval. *Disconfirmed if* a
  model's intervals are disjoint — in which case the balanced design
  changed the answer, and every an1 rate is reported as design-conditional.

## What will not be claimed

The census is single-reference Company-overview-dominated; it cannot speak
to multi-document synthesis. RP has no graph conditions, so it validates
the gate and contamination rates only — graph comparisons remain
design-conditional. No per-category claims from RP below n=20.

## Analysis fixed in advance

    export_snapshots --tag s4 --arms A,C  ·  materialize_anchors --tag s4
    answering --tag an3 --case-file dataset/reasoning_census_cases.txt
    answering --tag an4 --case-file dataset/representative_sample_cases.txt
    paired bootstrap over cases, 5,000 draws, per model — the an1 code path
