---
draws_on:
  - log2026.schema_sources.v1
  - log2026.validity.v1
  - log2026.schema_legibility.v1
  - log2026.shacl_check.v1
  - log2026.category_load.v1
  - log2026.arm_results.v2
---

# 2.1  The schema an agent queries with should come from the ontology, and usually does not

## The step everyone skips

A text2cypher system needs to tell the model what is in the graph. Standard
practice is to ask the graph: introspect the store, stringify the labels,
relationship types and property keys it reports, and put that in the prompt. The
published work on this asks how to *serialise* that description — JSON against
XML — how to *prune* it per question, and how to *compress* it to save tokens.
All of it takes the introspected schema as the thing to be described.

But the graph was built from an ontology, and the two are not the same document.
Introspection reports what an extractor happened to produce. The ontology
reports what was supposed to be produced. Where a language model did the
extracting, the gap between them is not small.

## How large the gap is here

Every number below is from the graphs this study loaded.

The ontology handed to the extractor in the FIBO condition declares 70 classes
and 12 relationship types. Introspection of the loaded graphs reports 96 labels
and 51 relationship types. Of those, 32 labels and 39 relationship types were
never declared — an agent handed the introspected description is told, as fact,
about structure the ontology forbids.

They are plausible names, which is what makes them dangerous rather than
obviously wrong: COGS, Dividend, EPS, Committee, Court. Nothing in the
description marks them as things the extractor invented.

The gap runs both ways, and the smaller direction matters too. Six declared
classes were never extracted, so a description built from the ontology alone
would send an agent looking for six things that are not there.

That the invented structure is not harmless is measurable independently. A
constraint check over the same graphs finds 584 violations in the FIBO condition
and 1,038 in the synonym condition where a relationship's endpoints are not the
classes its type declares. The membership test that runs during extraction
counts 5,521 uses of a name the ontology never declared.

The property side is the same story. Of the property keys in the loaded
databases, bookkeeping keys are used 35,718 times against 25,807 for the ones a
question could be about. A description built by introspection is mostly
workspace identifiers and provenance, crowding out the handful of properties a
financial question actually concerns.

## Why this closes the argument rather than opening a new one

Part 1 put the ontology where everyone puts it — in front of the extractor —
and found it did not help. Agreement between independently built graphs was
0.3389 with no schema and 0.2030 with real FIBO, separated from zero.

The reading that follows is not that the ontology is useless. It is that it was
applied at the stage where it governs the wrong thing. Extraction needs
instance-level agreement, which a class vocabulary cannot supply. **Querying
needs a class vocabulary, which is exactly what an ontology is.** The agent
writing a query has to know what kinds of thing exist and how they may relate,
and that is the ontology's actual content.

So the same artifact fails in one position and may be necessary in the other.
That is the claim, and it is falsifiable.

## The experiment

One factor: **where the schema description comes from**. Everything else — the
question set, the model, the retrieval, the answering prompt — is held fixed.

| Condition | The description is built from |
|---|---|
| introspected | what the store reports, the standard practice |
| declared | the ontology's classes, relationships and properties |
| declared ∩ present | the ontology restricted to what was actually extracted |
| introspected + types | introspection plus which properties are comparable |

Built and compared already, before any agent runs. Under the FIBO condition the
four descriptions come to 267, 112, 102 and 320 approximate tokens. The
declared-and-present description is the smallest, and its saving is not the
compression the literature discusses — that shortens the same information. This
removes information that is wrong.

The third exists because the second has an obvious failure mode and pretending
otherwise would be dishonest: an ontology declaring 70 classes of which 17 were
ever instantiated will send an agent looking for 53 things that are not there.
If `declared` loses to `declared ∩ present`, that is the reason, and it is worth
reporting because it says the ontology alone is not sufficient either.

Each description is produced by a **rule computed from the graph or the
ontology**, not written by hand. "The labels covering ninety percent of nodes"
is a function; "the prompt that worked" is not a contribution. This also
separates the approach from question-conditioned pruning in the literature: the
description is built once, not retrieved per question, so there is no retrieval
step to cost or to blame.

## What is measured, and how little of it needs a judge

Not accuracy alone. Accuracy says which condition won; the failure modes say
what each element of the description was doing.

| Failure | Decided by | Judge |
|---|---|---|
| the query does not parse | the parser | no |
| it names a label that does not exist | the schema | no |
| it names a property that does not exist | the schema | no |
| it compares a text property with an operator for numbers | the query and the property type | no |
| it runs and returns nothing | the row count | no |
| it returns rows and the answer is wrong | the gold answer | numeric answers, no; prose, yes |

Five of six are mechanical, and the sixth is mechanical wherever the answer is a
figure. That matters here specifically: this project has measured its own judges
disagreeing with each other, so a design that leans on them is a design that
inherits that.

## The control that makes an empty result readable

A query returning nothing means either that the description was poor or that the
graph has no such fact, and those are opposite conclusions. So the question set
is built from the graph, in two halves:

- **answerable**: questions constructed from facts the graph is known to hold,
  so an empty result is a failure of the description
- **unanswerable**: questions about facts the graph is known not to hold, so a
  non-empty result is the system inventing one

Without the second half a condition can win by being credulous. With it,
answering when it should not is counted, and the two halves are reported
together.

## What would disconfirm this

If the introspected description matches or beats the declared one on every
failure mode, the claim is wrong and the standard practice is right. If
`declared` loses badly to `declared ∩ present`, the ontology needs the graph to
be useful and cannot be the description on its own. If the type hint removes
type-error comparisons but does not reduce empty results, then the description
was never the binding constraint and the data shape was — which is the outcome
section 1.6 would predict and would be worth reporting against ourselves.

## What this does not establish

The gap between introspected and declared schemas is measured on graphs a
language model built. A curated graph would have a smaller gap, possibly none,
and nothing here says how much of the effect survives when extraction is
reliable.

This is also one corpus, one ontology and one query language. The mechanism —
that introspection reports an extractor's accidents as though they were the
schema — should generalise, but that it does is an argument and not a
measurement.
