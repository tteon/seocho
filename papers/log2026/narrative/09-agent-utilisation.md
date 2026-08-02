---
draws_on:
  - log2026.schema_sources.v1
  - log2026.schema_legibility.v1
  - log2026.category_load.v1
---

# 2.2  Providing the ontology is not the same as the agent using it

## The distinction the experiment turns on

Section 2.1 asks where the schema description should come from. It can be
answered by end accuracy, and answering it that way would be a mistake.

Giving an agent the ontology, the schema, a few worked examples and the
competency questions is providing information. Whether the query it then writes
was shaped by any of that is a separate fact, and end accuracy cannot separate
them. A condition that scores badly has two possible diagnoses with opposite
remedies:

|  | the agent used it | the agent ignored it |
|---|---|---|
| **it helped** | the information was right and was used | — |
| **it did not help** | **the information was wrong** — fix the description | **the agent did not use it** — fix the interface or the model |

Every published comparison of schema representations reports the row and leaves
the column unmeasured. That is the gap.

And it matters more here than usually, because the data behind these graphs is
now good enough that failure to retrieve it is the binding constraint. Building
a graph well and being unable to query it is the same as not having built it.

## Utilisation, measured on the query rather than inferred from the score

Each thing supplied leaves a trace in the generated Cypher if it was used. Those
traces are mechanical to check — no judge, no interpretation.

| Supplied | Used, if the query… | Ignored, if it… |
|---|---|---|
| declared schema | names only labels and relationship types the ontology declares | names one the extractor invented |
| type information | compares with `value_numeric` | compares with `value`, which is text |
| provenance | projects `_anchor_offset`, `_anchor_window` when asked to attribute | returns a value with nothing to cite |
| competency questions | matches the shape of one — same traversal, same aggregation | invents an unrelated shape |
| few-shot examples | reuses a clause pattern from one | shares no structure with any |
| category isolation | scopes to the database or `_category` the question concerns | queries across categories the question did not ask about |

Each is a property of the query text or its parse, so utilisation is a count and
not an opinion.

## The control that detects the null

The strongest single check is cheaper than any of the above: **run the same
question under two descriptions and diff the generated Cypher.**

If the query is identical whether or not the ontology was supplied, the ontology
never entered the decision, and no accuracy difference between those conditions
can be attributed to it. A design that cannot detect that outcome cannot
distinguish a description that did nothing from one that did nothing useful.

This is reported before any accuracy number: **the share of questions whose
query changed at all between conditions.** If that share is small the rest of
the comparison is noise, and saying so is the honest result.

## Three stages, because they fail differently

"Understanding" is too vague to measure. Operationally it separates into three
stages, each checkable and each with its own remedy:

**Grounding** — does the query name only things that exist? A query naming
`COGS` when no such label is declared is ungrounded. Checked against the schema.
Fails when the description contains invented structure, which is precisely what
introspection supplies.

**Selection** — does it name the *right* things for this question? A grounded
query can still traverse the wrong relationship. Checked against the entities
and relations the question is about, which are known because the question set is
built from the graph.

**Composition** — does it combine them correctly: the filter, the join, the
aggregation? A query can be grounded and well-selected and still count when it
should sum. Checked by executing it and comparing rows, not by reading it.

A condition can improve one stage and worsen another. The declared description
should improve grounding by construction, since it cannot mention invented
labels. Whether it improves selection is an open question, and whether it
improves composition is probably unrelated to the description at all — which,
if it holds, bounds how much the schema description can ever be worth and is
worth reporting for that reason.

## What is supplied, as conditions

Five things can be given, and they are not one condition. Supplied
independently so their contributions can be separated:

    schema source      introspected · declared · declared and present
    type information   absent · present
    worked examples    none · three · three drawn from competency questions
    competency set     absent · present as query templates
    provenance         hidden · exposed as queryable

The full cross is thirty-six cells and will not be run. The schema source is
swept first with everything else at its simplest setting; the remainder are
added one at a time to whichever source wins. Each addition is kept only if it
moves a utilisation count, not only an accuracy number — an addition that
improves the score without being used is a coincidence to be investigated
rather than a finding.

## Cost, reported beside every condition

The declared description is 102 approximate tokens against 267 for the
introspected one. A condition that improves accuracy while tripling the prompt
has a different value from one that improves it while shrinking it, and a table
reporting only accuracy hides that. Tokens per query and per answered question
go in the same table as the score.

## What would disconfirm the section's premise

If utilisation is high under every condition — the agent uses whatever it is
given — then the column collapses and end accuracy was sufficient after all.
If utilisation is uniformly low, the interface is the binding constraint and no
schema description will matter until that is fixed, which would redirect the
work rather than refine it.

Either outcome is more useful than an accuracy table, and neither is visible
from one.

## What this does not establish

Utilisation is evidence that supplied information shaped the query, not that it
shaped it correctly. A query can use a declared label and use it wrongly, and
the three stages exist to catch that rather than to be summed into one number.

The traces are also specific to Cypher and to this schema. The distinction
between providing and using generalises; the particular checks do not.
