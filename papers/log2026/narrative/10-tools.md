---
draws_on:
  - log2026.question_axes.v1
  - log2026.fact_anchors_summary.v1
  - log2026.schema_legibility.v1
  - log2026.routing_ceiling.v1
---

# 2.3  The graph is one tool, and adding a calculator moves the error rather than removing it

## What an ontology could contribute, and how often it is asked to

An ontology carries three separable things: a vocabulary, so a term in a
question can be matched to a differently-worded term in a filing; a notion of
quantity, so figures can be compared; and a structure, so facts in different
places can be joined. Which of the three a question needs is not a matter of
opinion — for two of them the dataset says so directly, and the third can be
derived.

Across all 5,703 questions:

| What the question needs | Questions | Share |
|---|---:|---:|
| structure — parts joined | 824 | 14.4% |
| quantity — a figure compared | 443 | 7.8% |
| **vocabulary — a term bridged** | **30** | **0.5%** |
| none of the three | 4,406 | 77.3% |

The three turn out to be disjoint in this corpus, which was not the design —
they were built as overlapping sets, and the absence of overlap is a property of
the questions rather than of the definitions.

## The vocabulary result, and why it is not a small number to skip past

Thirty questions out of 5,703 need FIBO's vocabulary to be answerable. All
thirty are in Governance, and all of them are the same two bridges: a question
writing CFO where the filing writes chief financial officer, nineteen times, and
CEO for chief executive officer, fifteen times.

This is the missing half of an earlier result. The synonym condition was not
distinguishable from plain FIBO, and the reason was never established. It is
this: there was almost nothing for the synonym layer to do. A bridge that is
never crossed carries no traffic however well it is built.

It also says something about which vocabulary would have mattered. The
abbreviations these questions actually use are not FIBO's. Sampling two thousand
questions, `rev` appears 327 times, `mgmt` 71, `EPS` 57, `capex` 21 — an
analyst's shorthand, not an industry standard. FIBO declares LLC and EBITDA
because those are terms the industry agreed on. Nobody standardises `mgmt`. A
vocabulary that helped here would have to be learned from the corpus, which is
the same register gap section 1.2 measured from the other direction, and it is
not what adopting an industry ontology gives you.

## Adding a calculator is the obvious move, and it relocates the failure

Four hundred and forty-three questions need arithmetic. The engineering answer
is a calculator: retrieve the figures, compute outside the database. It works,
and it is standard practice.

What it does not do is remove the step where the error lives. A calculator needs
numbers as input. If retrieval returns "$5.2 billion" as text, something still
has to turn that into a number, and moving that step from the query into the
agent does not make it safer — it makes it quieter.

| Where the conversion happens | When it goes wrong |
|---|---|
| in the query, comparing a text property | **an empty result** — visible, and reads as "no such fact" |
| in the agent, parsing before computing | **a confident wrong answer** — invisible |

An empty result is a failure anyone can see. A wrong number is a failure nobody
sees. Adding a calculator trades the first for the second.

This is not speculation about what models might do. A quarter of the figures in
these graphs — 494 of 1,975 anchored — differ from the number printed in their
source by a factor of a thousand or a million, because a model applied a table's
units or failed to. The conversion is already going wrong at that rate inside
extraction. There is no reason to expect it to go better inside an agent, and
every reason to expect the failures to be harder to notice.

The remedy is not to withhold the calculator. It is to give it numbers instead
of strings, which is a loading decision and not a tool decision.

## The graph as one tool among several

Treating the database as one tool rather than as the system corrects the
comparison. The question is not whether a graph beats an index. It is what each
tool contributes that no other does, and for the graph the honest list is short,
because retrieval is not on it: one view already holds nearly all the figures
three views hold, and a perfect router beats a fixed choice by almost nothing.

What remains is specific:

- **provenance** — a served figure can name the passage and offset it came from
- **contradiction** — two views that read one printed number differently can be
  found, which name matching cannot do
- **joins and aggregation without a model** — the cost argument, at scale

None of those is retrieval, and none is replaced by a calculator.

## The conditions

Tool set as the factor, and the third and fourth rows are the ones the argument
turns on.

| Tools | Expected failure |
|---|---|
| graph only, arithmetic in Cypher | text comparison returns nothing |
| graph + calculator, figures as text | the agent parses, and parses wrong, silently |
| graph + calculator, figures parsed at load | no parsing step remains |
| graph + calculator + provenance | as above, and disagreements surface |

The second against the third is the measurement. The same calculator, the same
questions, the same model — and the only difference is whether the figure
arrived as a number. If silent wrong answers fall between them, then adding a
tool did not substitute for fixing the data, and that is the claim.

One thing this design has to be careful about: with a calculator in the loop, a
wrong answer could be retrieval's fault or the calculator's. So what is passed
into the calculator is recorded, which separates the two the same way section
2.2 separates supplying information from using it.

## What this does not establish

The three axes are disjoint here and need not be elsewhere. Numeric and
structural are only annotated in Financials and Company overview, so their
absence in the other six categories means unannotated, not absent — no rate in
the table above should be read as a property of the whole corpus.

The vocabulary axis detects a lexical bridge, not that answering requires
crossing it. Thirty is a floor on how often FIBO's vocabulary is needed, not a
measurement of how often vocabulary of any kind is.
