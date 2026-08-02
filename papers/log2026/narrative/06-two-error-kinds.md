---
draws_on:
  - log2026.shacl_check.v1
  - log2026.fact_anchors_summary.v1
  - log2026.provenance_keying.v1
  - log2026.validity.v1
---

# 1.6  Two kinds of error, two mechanisms, neither substituting for the other

The usual claim made for an ontology in this setting is that it catches what a
language model gets wrong. Our results say that is true of one kind of error and
false of another, and the distinction is worth more than either half.

Separating the failures the extraction actually produces:

| Kind | What it is | Scale |
|---|---|---|
| **naming divergence** | two models call the same fact different things | the majority |
| **value misreading** | a model writes a figure the source does not print | 494 of 1,975 anchored figures |
| **schema violation** | a relationship's endpoints are not the classes its type declares, or a class's required property is missing | 529 to 1,038 per condition |

Only the third is what an ontology is for.

## The ontology does catch something, and nobody is asking it to

A real constraint checker over the extracted graphs, with shapes generated from
each condition's own declared classes and relationships:

| Condition | Undeclared names | Endpoint and required-property violations |
|---|---:|---:|
| no ontology | 4,856 | 18 |
| real FIBO | 5,521 | 584 |
| FIBO + synonyms | 5,747 | 1,038 |
| FIBO + hierarchy | 5,280 | 529 |

The right-hand column is the first positive result in this study. Those are
violations of constraints the ontology states — an edge between the wrong
classes, a node missing a property its class declares required — and every one
of them was written to the graph.

They were written because nothing asked. Validation defaults to off in the
pipeline, and the check that runs when it is on tests set membership: is this
label in the declared list, is this relationship type in the declared list.
That test cannot express an endpoint constraint at any count. The ontology
already knew; the pipeline never enquired.

The left-hand column is not the same measurement at a different strength.
Membership counts every use of an undeclared name while a node shape reports
once per node, and a relationship type the ontology never declared has no shape
at all, so the checker is structurally unable to object to it. The two columns
see different things and neither bounds the other.

## The ontology cannot catch the error that corrupts answers

A quarter of anchored figures — 494 of 1,975, a share of 0.2501 — match their
source only after rescaling. 279 differ from the printed number by a factor of a
thousand and 213 by a factor of a million. Those are models applying, or
declining to apply, a table's units.

No class declaration reaches this. An ontology governs what kinds of thing exist
and how they may relate; it does not govern the mapping from a printed numeral
to a value, because that mapping lives in a header three rows above the number
and is not a property of the concept at all. FIBO could declare `MonetaryAmount`
perfectly and two extractors would still disagree by a thousandfold on the same
figure.

What does reach it is the source. Anchored to the token they came from, the same
graphs under real FIBO yield 183 comparable pairs against 30 by name, and 87
disagreements against 2 — every one of the 87 invisible to name matching,
because the two views had given the fact different names.

## And the ontology makes the first kind worse

Naming divergence is neither caught nor left alone. Every condition given a
schema agrees less than the condition given none — 0.2030, 0.2100 and 0.2425
against 0.3389 — and each of those differences is separated from zero by a
case-resampled interval.

## The reading

Two mechanisms, doing different work:

- **Constraints** catch structural error. They are already specified, already
  violated at a rate of hundreds per condition, and simply not consulted.
- **Provenance** catches value error. It is not specified anywhere, has to be
  recovered after the fact, and is what makes the disagreements that matter
  visible at all.

Neither substitutes for the other, and neither addresses naming divergence,
which the ontology aggravates. A system that wants to catch what a model gets
wrong in this domain needs both, and calling either one "catching hallucination"
flattens a distinction the measurements are clear about.

## What this does not establish

"Hallucination" is also the wrong word for most of what is counted here. A model
that reads a table's "in thousands" header and multiplies has not invented
anything; it has made a defensible reading, and which of two readings is correct
depends on a header rather than on the model. The schema violations are
structural errors, and whether the underlying fact is also wrong is a separate
question this does not answer.

The constraint shapes cover three kinds — required properties, the declared
class set, relationship endpoints — and not cardinality beyond minCount,
datatypes or disjointness. The violation counts are a floor on what the ontology
could catch, not a ceiling.
