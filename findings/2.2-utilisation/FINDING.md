# 2.2  Providing the ontology is not the same as the agent using it

**· not yet run**

> No pre-registration file. The hypothesis below was written up alongside the analysis rather than committed before the run, and should be read as a statement of intent recovered after the fact.

## Question

When an agent writes a query badly, was the information it was given wrong, or did it not use the information?

## Hypothesis

End accuracy cannot separate those two, and they have opposite remedies. Whether supplied information shaped a query leaves a mechanical trace in the query itself, so utilisation can be counted rather than inferred from the score.

## Method

Not yet run. Each thing supplied — the declared schema, type information, provenance, competency questions, worked examples, category scoping — leaves a checkable trace in the generated Cypher if it was used. The strongest single check is cheaper than any of them: run one question under two descriptions and diff the query. Failure is then separated into grounding, selection and composition, which fail differently and have different remedies.

## Measured

Not run.

## Reading

_Interpretation, separate from the measurement above._

Nothing is measured here yet, and the section exists because the measurement it describes is the one the rest of Part 2 depends on.

The reason to build it before running any comparison: a schema description that produces a worse score has two diagnoses. The description was wrong, or the agent never read it. Every published comparison of schema representations reports the score and leaves that unresolved, and the remedies point in opposite directions — fix the description, or fix the interface.

The null this must be able to detect is that the query does not change at all between conditions. If the same question yields the same Cypher whether or not the ontology was supplied, then no accuracy difference between those conditions can be attributed to it, however large. That check is a byte comparison and comes before any score.

## What this does not support

Utilisation is evidence that supplied information shaped a query, not that it shaped it correctly. A query can use a declared label and use it wrongly, which is what the three stages are for rather than a single number. The traces are specific to Cypher and to this schema; the distinction between providing and using generalises, the checks do not.

## Still needed before this section is complete

- a question set built from the graph, answerable and unanswerable, so an empty result can be read
- the query-writing agent itself, run under each description
- the utilisation traces, one per supplied element

## Reproduce

```bash
not yet implemented
```

---

## Draft notes

<!-- authored: kept across regeneration -->
_Nothing yet. Text written between the two markers survives `findings.py --write`._
<!-- /authored -->
