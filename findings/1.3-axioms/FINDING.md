# 1.3  Does reasoning add structure the class list does not have?

**✔ supported**

> No pre-registration file. The hypothesis below was written up alongside the analysis rather than committed before the run, and should be read as a statement of intent recovered after the fact.

## Question

The extraction prompt receives a flat list of class names. Would entailment give it more?

## Hypothesis

FIBO carries restrictions, unions and equivalences, so a reasoner should place classes under one another and resolve relation endpoints that the asserted axioms leave open.

## Method

An OWL 2 RL closure over the FIBO turtle, comparing the subclass, equivalence, disjointness and domain/range relations before and after, restricted to the classes the FIBO condition actually ships.

## Measured

| | |
|---|---|
| classes with a parent in scope | 7 → 15 |
| relations with both endpoints resolved | 4 → 28 |
| subclass edges added within scope | 84 |
| equivalences added within scope | 70 |
| disjoint pairs available | 2 |

Artifact: `outputs/minimal/20260802T013239Z-reasoner-pretest/reasoner_pretest.json`
Trace: `outputs/minimal/20260802T013239Z-reasoner-pretest/trace.jsonl`

## Reading

_Interpretation, separate from the measurement above._

Supported, and it justified adding a fifth condition. Within the seventy classes the FIBO condition ships, entailment more than doubles the classes that have a parent inside that set, from 7 to 15, and takes relations with both endpoints resolved from 4 to 28. My own hand-written parent walk had found 12 of those 28, so the approximation I was using was missing more than half.

The hierarchy it produces is directly relevant to the comparison problem: ChiefExecutiveOfficer under Employee and Executive, Lease under Agreement and Contract, Debt under Commitment. Two views answering ChiefExecutiveOfficer and Executive for one person are a mismatch under string equality and compatible under subsumption, so the hierarchy changes what the agreement measure is measuring, not only how much of it there is.

Everything here is a floor. HermiT and Pellet need a JVM this machine does not have, so the engine is a pure-Python OWL 2 RL closure, and RL does not derive subsumption from complex class expressions. FIBO leans on those heavily — 2,656 restrictions and 1,344 someValuesFrom in the quickstart — so a complete reasoner would find strictly more.

## What this does not support

OWL 2 RL, not DL. Counts what entailment adds to the schema; says nothing about whether a richer schema improves extraction.

## Still needed before this section is complete

- a DL reasoner, to turn these floors into values

## Reproduce

```bash
python3 experiments/minimal/reasoner_pretest.py
```

---

## Draft notes

<!-- authored: kept across regeneration -->
_Nothing yet. Text written between the two markers survives `findings.py --write`._
<!-- /authored -->
