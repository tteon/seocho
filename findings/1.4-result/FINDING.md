# 1.4  Does giving the extractor an ontology make two models agree?

**✘ rejected**

## Question

Holding documents, prompt, chunking and seed fixed, does the schema handed to the extractor change how often two models describe the same fact under the same name?

## Hypothesis, written before the run

Pre-registered: agreement rises from no ontology, through the hand-written schema, to real FIBO, and highest with the synonym layer. More shared vocabulary, more shared naming.

## Method

Four conditions differing only in the schema. Sixteen cases, three extractor models, every extraction scored. A fact is comparable when at least two of the three models produced the same normalized name within the same case.

## Measured

| | |
|---|---|
| condition A | 0.375 (500 of 1334) |
| condition B | 0.221 (232 of 1051) |
| condition C | 0.193 (323 of 1672) |
| condition D | 0.201 (341 of 1699) |

Artifact: `outputs/minimal/20260802T025158Z-arm-results/arm_results.json`
Trace: `outputs/minimal/20260802T025158Z-arm-results/trace.jsonl`

## Reading

_Interpretation, separate from the measurement above._

Rejected, and in the opposite direction. Giving the extractor no ontology at all produced the highest agreement, 0.375, against 0.221 for the hand-written schema and 0.193 for real FIBO. Keyed on the model's own identifier rather than the name, the gap is wider still: 0.160 against 0.052.

The mechanism is visible in the counts rather than the rates. Seventy classes produced 1,672 distinct fact names where no schema produced 1,334 — 25% more — and nearly every additional name was seen by one model only. Giving an extractor more ways to slice a sentence lowers the chance two extractors slice it the same way. That is a claim about mechanism and it is interpretation, not measurement; the class-count control that would confirm it has not run.

The synonym layer edges plain FIBO, 0.201 against 0.193, which is the direction it predicts. It is eighteen keys. At this sample size that is not a result and is reported as a direction only.

Two columns of the original table must not be read and the scripts now say so. The declared-type share is meaningless for the no-ontology condition, whose single declared class is Entity, so 0.988 is a definition. The period fill rate was confounded: the FIBO conditions received a property set I wrote by hand while the baseline declared only a name, so filling `period` twice as often measures which condition was given the slot. The second run equalizes the property floor across all conditions and adds the subsumption condition.

## What this does not support

Sixteen cases, three models, one run, no confidence interval. Measures whether two models NAME a fact the same way, not whether they captured the same fact, and not whether either is correct.

## Still needed before this section is complete

- a confidence interval, before any difference is stated as a result
- a correctness measure, since a schema could lower agreement and raise accuracy
- a content measure, since agreement on names is not agreement on facts

## Reproduce

```bash
scripts/ops/run_reextract.sh --cases 16 && python3 experiments/minimal/arm_results.py && python3 experiments/minimal/plot_arms.py
```
