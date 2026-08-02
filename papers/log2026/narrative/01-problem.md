---
draws_on:
  - log2026.merge_key_reality.v1
---

# 1.1  Without a schema, the output depends on which model ran

Three models were given the same filings and told to extract a graph, with no
schema and nothing else to go on. Each produced a graph. None of the three can
be joined to another.

The reason is not that they disagree about the facts. It is that the graph
merges on an identifier, and the identifier is a string the model invented.

## Measured

For every node, the identifier the model wrote is compared against the slug of
the name it gave that same node. Where the two agree, the identifier was derived
from something in the document and another model reading the same document has a
chance of producing it. Where they diverge, the identifier came from the model.

| View | Identifier derivable from the name | Sampled |
|---|---:|---:|
| DeepSeek-V3.1 | 0.4247 | 60,000 |
| MiniMax-M2.7 | 0.2660 | 60,000 |
| gpt-oss-120b | **0.0598** | 60,000 |

gpt-oss is the extreme and the reason is systematic rather than random: it
prefixes a type abbreviation onto almost everything it writes. `ma_` for
monetary amounts appears 1,867 times, `fm_` for financial metrics 1,772. Neither
of the other two models does this. DeepSeek's own habits are milder but present:
`eps_` on 427 nodes, `us_` on 124.

## Why this is the whole problem in miniature

The store merges on `(identifier, workspace)`. Two models that write
`ma_repurchase_amount_2022` and `repurchase_amount` for the same figure produce
two nodes that can never meet, however identical the underlying fact and however
correct both extractions are.

Nothing downstream can recover from this. A federation that cannot tell when two
of its views are talking about the same thing cannot compare them, cannot detect
that they disagree, and cannot attribute a served value to more than one source.
Every limit measured later in this study is downstream of this one.

## What this does not establish

The comparison is between models. It does not show that the disagreement is
*caused* by the models being different, because a single model run twice may
disagree with itself by a similar margin — in which case the finding is about
sampling temperature rather than about what each model learned. That run has not
happened, and this section is incomplete without it.

The measurement is also about naming and not about content. Two graphs could
disagree on every identifier and hold exactly the same facts. Section 1.4
addresses that separately, and the answer turns out to matter.
