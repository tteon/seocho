# 2.1  The schema an agent queries with should come from the ontology

**~ undecided**

> No pre-registration file. The hypothesis below was written up alongside the analysis rather than committed before the run, and should be read as a statement of intent recovered after the fact.

## Question

How far apart are the schema description a text2cypher system is usually given and the ontology the graph was built from, and does the difference change what an agent can ask?

## Hypothesis

Standard practice builds the description by introspecting the store, which reports what an extractor produced rather than what was specified. Where a language model did the extracting the two diverge, and an agent told about structure the ontology forbids will write queries against it.

## Method

Four descriptions built by rule rather than by hand — introspected, the ontology's own vocabulary, the ontology restricted to what was extracted, and introspection with comparable properties marked — compared on size and on what each contains that the others do not. The agent half, where each description is given to a query-writing model and its failures are counted by kind, has not run.

## Measured

| | |
|---|---|
| declared | 70 labels, 12 relationships |
| introspected | 96 labels, 51 relationships |
| present but never declared | 32 labels, 39 relationships |
| declared but never extracted | 6 |
| bookkeeping keys introspection includes | 21 |

Artifact: `outputs/minimal/20260802T070908Z-schema-sources/schema_sources.json`
Trace: `outputs/minimal/20260802T070908Z-schema-sources/trace.jsonl`

## Reading

_Interpretation, separate from the measurement above._

The two sources differ by more than enough to matter, and the direction is the one that damages a query.

Under the real-FIBO condition the ontology declares 70 classes and 12 relationship types. Introspection reports 96 labels and 51 relationship types. Thirty-two of those labels and thirty-nine of those relationship types were never declared: an agent handed the introspected description is told, as fact, about structure the ontology forbids. They are plausible names — COGS, Dividend, EPS, Committee, Court — which is what makes them dangerous rather than obviously wrong.

The reverse gap is smaller but real. Six declared classes were never extracted, so the ontology alone would send an agent looking for things that are not there. That is why the third description exists, and why a result showing the ontology sufficient on its own would be surprising.

The description also shrinks, 267 approximate tokens to 102. That is not the compression the literature discusses, which shortens the same information. This removes information that is wrong.

None of this is yet a result about querying. It establishes that there is something to test and how large it is. Whether an agent given the declared description writes better queries is the measurement, and it has not been made.

## What this does not support

A static comparison of descriptions. It says the two sources differ; it does not say the difference changes what an agent can retrieve. The gap is also measured on graphs a language model built — a curated graph would show less of it, and nothing here says how much of the effect survives when extraction is reliable.

## Still needed before this section is complete

- the agent half: each description given to a query-writing model, failures counted by kind
- a question set built from the graph in two halves, answerable and unanswerable, so an empty result can be read

## Reproduce

```bash
python3 experiments/load_categories.py --tag v2 --arms C && python3 experiments/schema_sources.py --tag v2 --condition C
```

---

## Draft notes

<!-- authored: kept across regeneration -->
_Nothing yet. Text written between the two markers survives `findings.py --write`._
<!-- /authored -->
