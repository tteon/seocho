# 1.0  Was separating the categories necessary?

**~ undecided**

## Question

If every category shared one graph, would merging on name fuse things that are not the same thing?

## Hypothesis, written before the run

Names collide across categories and mean different things, so a shared graph would silently fuse unrelated facts and the per-category databases are necessary rather than cautious.

## Method

Per-category databases are read for every distinct entity name and every relationship type. Names present in more than one category are embedded together with their graph context — label, value, neighbour names — using a local model, and their cosine similarity is compared against a control: the similarity of unrelated nodes inside a single category.

## Measured

| | |
|---|---|
| distinct entity names | 3,865 |
| in more than one category | 65 (1.7%) |
| excluded as non-entities | 13 |
| relationship types shared | 21 of 28 |
| context similarity, shared names | 0.700 |
| control, unrelated nodes | 0.689 |

Artifact: `outputs/minimal/20260802T024329Z-category-contamination/category_contamination.json`
Trace: `outputs/minimal/20260802T024329Z-category-contamination/trace.jsonl`

## Reading

_Interpretation, separate from the measurement above._

The hypothesis is not supported in the strong form it was written. Only 1.7% of entity names appear in more than one category, and for those the context similarity is 0.700 against a control of 0.689 — a difference of about one hundredth.

Both readings of that are worth stating because they point different ways. In favour of separating: a colliding name carries essentially no guarantee of shared meaning, since two nodes with the same name sit in surroundings no more alike than two arbitrary nodes do. Merging on name would therefore be fusing on a coincidence. Against: there are only 65 such names, so the practical exposure is small.

The relationship side splits from the entity side and does support the claim: 21 of 28 relationship types appear in more than one category. Entities barely overlap while the edges between them overlap almost completely.

There is a structural reason the entity overlap is low that has nothing to do with contamination: each category holds different filings about different companies, so most names could not collide even in principle. The sharp version of this test restricts to companies that appear in two categories and asks whether their metric names mean the same thing there. Until that runs, this section cannot claim isolation was necessary.

## What this does not support

Context similarity is a proxy for meaning computed from the extracted graph, not from the source text. The categories hold different source documents, which depresses overlap for reasons unrelated to the question.

## Reproduce

```bash
python3 experiments/minimal/category_contamination.py
```
