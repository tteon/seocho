# Competency questions — draft

Ontology engineering asks for these before the ontology is chosen: the questions
the ontology must let you answer, written so that success and failure are
distinguishable. We did not write them. FIBO was adopted and modules were picked
without stating what they had to support, and every ontology result since has
been reported without a criterion for judging it.

That gap now matters concretely. The measurement says entities carrying an
ontology-declared type appear in two or more views **4.5 times less often** than
untyped ones (.050 against .227). Whether that is the ontology working or the
ontology failing cannot be answered without saying what it was supposed to do.

This draft is written against what the corpus can actually answer, so each
question has a query and an outcome, not an aspiration. Status is honest: several
are already failed.

---

## Why an ontology at all, and why FIBO

The federation makes exactly one demand of a schema: **two independently built
graphs must be able to say they are talking about the same thing.** Everything
the paper claims about verification depends on that and nothing else.

An ontology is the candidate mechanism because it fixes class and property names
ahead of extraction, so two extractors can converge without coordinating. FIBO is
the candidate ontology because the corpus is SEC 10-K text, FIBO is the industry
reference model for exactly that domain, and it is maintained by the EDM Council
rather than by us, which keeps the schema from being tuned to our results.

Both of those are hypotheses. CQ1 and CQ2 test the first; CQ7 tests the second.

## The questions

Answerable means: a query over the frozen graphs returns a definite answer.
Passing means: the answer is the one the design needed.

| # | Question | How it is answered | Status |
|---|---|---|---|
| CQ1 | Given a fact in one view, can I tell whether another view describes the same fact? | Match on `(case, fact slug)`; report the comparable-key rate | **Answerable, failing.** 8.0% of facts, 5.8% of entities |
| CQ2 | When two views describe the same fact, can I tell whether they agree? | Normalize scale and units, compare numerically | **Answerable, passing.** 18,281 pairs compared, 23.5% disagree |
| CQ3 | When they disagree, can I say how? | Classify by ratio and sign | **Answerable, passing.** 67% are scale errors |
| CQ4 | Can I attribute any served value to a view and a source document? | Every fact carries `workspace_id` and `source_id` | **Answerable, passing** |
| CQ5 | Can I decide, without a model, whether a slot is safe to serve? | Conflict set plus protected-field list | **Answerable, passing.** Deterministic, 60/60 real conflicts surfaced |
| CQ6 | Can I tell whether an entity carries a declared type or a fallback? | Label set against the composed class set | **Answerable, passing** |
| CQ7 | Does declaring a type make an entity more findable across views? | Overlap rate under an ontology arm against a no-ontology arm | **Withdrawn pending the arm run.** See below |
| CQ8 | Can I tell which team owns a fact, and whether a caller may read it? | View identity is the owner; authorization is external | **Partly.** Ownership yes, authorization is assumed, not modelled |
| CQ9 | Can I compare the same metric across periods for one issuer? | Requires `period` on the fact node | **Not answerable.** `period` is largely unpopulated; the year lives inside the slug |
| CQ10 | Can I resolve two names for one company to one identifier? | Ticker-supported must-link | **Partly.** Ticker only; no CIK or LEI, so coverage is partial |

## What the failures mean

**CQ1 is the load-bearing failure.** The ontology fixes class names, so both
extractors agree that a thing is a `MonetaryAmount`. It does not fix *instance*
identity, so one writes `shortterminvestments_2023` and the other writes
something else, and the two never meet. FIBO was adopted to make views
comparable and it does not do that, because the layer that needs to align is
below the class layer.

**CQ7 was measured wrongly and the earlier reading is withdrawn.** Comparing
declared-type entities (.050) against generic-fallback entities (.227) inside one
graph is not an ontology contrast. What decides whether an entity receives a
declared type is what kind of thing it is: companies get `LegalEntity`, one-off
figures get `MonetaryAmount`. The comparison therefore contrasts coarse entities
with fine ones and attributes the gap to typing. The observation that coarse
entities recur across views and fine ones do not still stands; the causal claim
about declaring a type does not. Only the arm run can settle it.

**CQ9 is our defect, not FIBO's.** FIBO has the vocabulary for periods; our
extraction did not populate it, so temporal comparison is impossible even though
the schema allows it.

## What the re-extraction arms decide

Arms hold documents, models, and prompt fixed and move only what the extractor is
given: **A** nothing, **B** the 151-term hand-written module set used for every
result so far, **C** real FIBO classes and labels, **D** C plus the synonym,
abbreviation, and preferred-designation layer.

Seven of the ten questions move with the arm. Three do not, because they are
properties of the architecture rather than of the vocabulary.

| # | Measure under the arms | Passes when |
|---|---|---|
| CQ1 | Comparable-key rate on `name` keys across the three models | An arm beats A, and beats B in absolute comparable keys |
| CQ2 | Disagreement rate among comparable pairs | Measurable at all, which needs CQ1 above the floor |
| CQ3 | Distribution of disagreement kinds | Scale errors remain identifiable, not swamped by noise |
| CQ6 | Share of entities carrying a declared type | Rises from A to C; distinguishes vocabulary size from typing |
| CQ7 | Entity overlap of arm C against arm A, paired by case | A real ontology contrast, replacing the withdrawn within-graph one |
| CQ9 | Fill rate of the declared `period` property | Rises when the ontology supplies the property and the prompt asks for it |
| CQ10 | Share of alias pairs merged to one identifier | D beats C, which isolates the SKOS synonym layer |

CQ4, CQ5, and CQ8 are unchanged by the arms. Provenance and the serving refusal
follow from the graph structure and the policy layer, and authorization is
external to the ontology in all four arms.

The primary decision is CQ1, because every other comparability result is
conditional on it. CQ7 and CQ10 are what make the run worth paying for: together
they separate *having a bigger vocabulary* from *having a synonym layer*, which
is the difference between citing FIBO and using it.

## Consequences for the paper

1. Every ontology result reported so far was produced with a 151-term
   hand-written subset, not with FIBO. Real FIBO carries 6,611 terms and a
   synonym layer of 2,127 annotations. Vocabulary coverage of the questions
   rises from .08-.28 to .42-.55 when the real vocabulary is used, so the
   earlier conclusion was about the subset and not about FIBO.
2. The honest finding is sharper than a claim of success would have been:
   **class-level agreement is not instance-level agreement**, and federation needs
   the second. That is a result about ontology-based federation in general, not
   about our pipeline.
3. CQ9 and CQ10 belong in limitations as extraction gaps, separate from the CQ1
   and CQ7 findings, which are properties of the approach.

## Open

- The reported snapshot composed `[acc, be, fbc, fnd, ind]`. This is now
  confirmed from the factorial run record rather than inferred, and it matches
  the set derived independently from the class labels present in the graphs.
- FIBO's competency questions are the same kind of question FinDER asks:
  mechanically generated competency questions reach mean best similarity
  .59-.72 against a within-corpus control of .78-.83. Risk is the exception at
  .59 with the highest control, .83, which marks it as outside FIBO's scope
  rather than as an ontology failure. The vocabulary measurement agrees:
  cybersecurity, cyber, and ERM are absent from real FIBO.
- CQ2 measures consistency between extractions, never accuracy against truth.
  No competency question here can be about correctness, because the corpus has
  no gold values.
