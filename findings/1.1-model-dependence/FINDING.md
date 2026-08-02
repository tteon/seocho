# 1.1  Without a schema, does the output depend on which model ran?

**✔ supported**

## Question

Do three models given the same document produce graphs whose identifiers can be matched to each other?

## Hypothesis, written before the run

With no schema the extractor falls back on its own pre-training, so different models will key the same fact differently and the graphs will not join.

## Method

For every node in each model's graph, compare the identifier the model wrote against the slug of the name it gave the same node. Divergence means the identifier was invented rather than derived, and an invented identifier cannot match another model's.

## Measured

| | |
|---|---|
| mdmdeepseek: identifier derivable from name | 42.5% of 60,000 |
| mdmgptoss: identifier derivable from name | 6.0% of 60,000 |
| mdmminimax27: identifier derivable from name | 26.6% of 60,000 |

Artifact: `outputs/minimal/merge_key_reality.json`
Trace: `outputs/minimal/trace.jsonl`

## Reading

_Interpretation, separate from the measurement above._

Supported, and the size of the effect is larger than expected. The identifier is derivable from the name in 42.5% of DeepSeek's nodes, 26.6% of MiniMax's and 6.0% of gpt-oss's. gpt-oss systematically prefixes a type abbreviation — ma_ for monetary amount, fm_ for financial metric, rev_ for revenue — which no other model does.

This is the mechanical cause of everything downstream. The graph merges on (identifier, workspace), so two models writing ma_repurchase_amount_2022 and repurchase_amount for the same figure produce two nodes that can never meet, however identical the underlying fact.

What this does not yet establish is whether the disagreement is between models or simply within them. A single model run twice may disagree with itself just as much, in which case the finding is about sampling temperature rather than about pre-training. That run has not happened and the section is incomplete without it.

## What this does not support

Measures naming, not content. Two graphs could disagree on every identifier and hold the same facts.

## Still needed before this section is complete

- a second run of one model, to separate between-model disagreement from within-model variance

## Reproduce

```bash
see outputs/minimal/merge_key_reality.json
```
