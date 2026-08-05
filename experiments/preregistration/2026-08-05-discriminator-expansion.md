# Pre-registration — the discriminator expansion (s5, an5, PR-)

Committed before any s5 extraction, any an5 call, and before the s4-based
profile prediction is scored. Prefix **PR-** (profile), **S5-**, **A5-**.

## The one purpose these runs share

Distinguish, with a measurable criterion, (a) when ontology-guided
EXTRACTION is appropriate and when it is not, and (b) when ontology-guided
ANSWERING (schema at generation time) is appropriate and when it is not.
Every prior run varied the ontology at extraction only; (b) has never had a
condition. The candidate criterion is the verifiability profile; its first
exploratory validation (per-category V vs. grounded-correct lift of the
graph over passages, an1, eight categories) measured Spearman rho = +0.762.

## PR — prospective profile prediction (the only chance left to be early)

an3 (reasoning census) has not run. Before it does:

1. After s4's deepseek extraction lands, compute the profile on s4 snapshots
   only (V, anchor rate, name-blind disagreement share) — no answering data
   exists for these cases at that point.
2. **PR-H1**: the reasoning census's V will fall below the balanced sample's
   (its cases are single-reference Company-overview-dominated, and CO's V
   was 0.500 vs corpus 0.44 avg... direction taken from s4 measurement, so
   the prediction registered here is CONDITIONAL: if s4's V(census) is
   below the an1 sample's pooled V, we predict graph-vs-passages
   grounded-correct lift on an3 at or below the an1 lift; if above, above.
   The registered content is the MONOTONE LINK, not a point estimate.
   *Disconfirmed if* the direction of the lift contradicts the direction of
   the V difference — which would falsify the profile as a predictor on its
   first prospective test.

## S5 — the full schema dose-response at scale (extraction axis)

Conditions B (hand-written 20 classes), D (C + synonyms), E (C +
subsumption) at the same 280 balanced cases, three models — completing the
five-point spectrum of which only A and C exist at scale. Same everything;
tag s5 isolates databases and workspaces.

- **S5-H1**: name-agreement is monotone in declared-class count at scale
  (A > B > {C,D,E}), reproducing the v2 ordering with intervals.
- **S5-H2**: D (synonyms) does not separate from C on any Part 1 measure —
  the vocabulary-demand null carries to scale.
- **S5-H3**: E (hierarchy) does not separate from C — v2's SW-H2 direction.

## A5 — ontology at answer time (the missing axis, 2x2)

New answering conditions on the balanced 280: {passages, graph_c} x
{with, without} a schema block (the same corpus-scoped FIBO class and
relation definitions the condition-C extractor saw, ~70 classes). Three
models, temperature 0, prompts otherwise identical; the schema block is the
only change. 280 x 2 new conditions x 3 models = 1,680 calls (the two
without-schema cells already exist in an1).

- **A5-H1**: the schema block does not raise number overlap on passages —
  the honest prior from every schema-in-prompt null this repository has
  measured. *Disconfirmed if* it separates upward: ontology-guided
  generation would then have value extraction never showed.
- **A5-H2**: the schema block does not reduce over-refusal on graph_c for
  the model whose deficit is over-refusal (deepseek). *Disconfirmed if*
  refusals drop with intervals separated — which would mean the
  serialization legibility failure is repairable by declaring the schema,
  a cheap and deployable fix.
- **A5-EC**: evidence-conditional replication as in the an2 addendum.

## What will not be claimed

n=8 category cells for PR's exploratory anchor; the prospective test is one
bit of direction, not a calibration. A5's schema block is one rendering of
one ontology; a null is a null for this rendering.
