# 1.5  Why does more vocabulary lower agreement?

**· not yet run**

## Question

Is the fragmentation caused by the number of classes, by FIBO's particular classes, or by declaring types at all?

## Hypothesis, written before the run

Declaring a type pushes the extractor toward specific, idiosyncratic instance names, so typed entities are less findable across views than untyped ones.

## Method

Not yet run. Requires an entity-overlap comparison between the no-ontology and FIBO conditions paired by case, an alias-collapse measure between the FIBO and synonym conditions, and a control condition with seventy classes that are not FIBO's.

## Measured

Not run.

## Reading

_Interpretation, separate from the measurement above._

Nothing here is measured yet, and one earlier attempt is withdrawn.

The withdrawn one matters because it was reported as a finding. It compared entities carrying a declared type (0.050 overlap) against entities carrying the generic fallback (0.227) inside a single graph, and read the gap as an effect of declaring a type. It is not. What decides whether an entity gets a declared type is what kind of thing it is — companies get LegalEntity, one-off figures get MonetaryAmount — so the comparison contrasts coarse entities with fine ones. The observation that coarse entities recur and fine ones do not still stands; the causal claim does not.

The control that separates 'more classes' from 'FIBO's classes' does not exist yet and is the one that decides whether the mechanism claim in section 1.4 can be made at all.

## Still needed before this section is complete

- type findability, no-ontology against FIBO, paired by case
- alias collapse, FIBO against FIBO-plus-synonyms
- a control condition with seventy non-FIBO classes

## Reproduce

```bash
not yet implemented
```
