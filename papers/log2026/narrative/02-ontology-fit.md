---
draws_on:
  - log2026.cq_similarity.v1
  - log2026.ontology_task_fit.v1
  - log2026.alias_register.v2
  - log2026.reasoner_pretest.v2
---

# 1.2  FIBO is the right ontology for these questions, and Risk is not in it

Before asking whether an ontology helped, it has to be established that the
ontology covers the task. A poor result on a category the ontology was never
built for is a scope mismatch reported as a failure.

Two independent checks, neither of which looks at any extraction.

## Does FIBO ask the same kind of question the corpus asks?

Competency questions were generated mechanically from FIBO's own classes,
properties and relationships — 323 of them — and compared by meaning to the real
questions. Generation is mechanical on purpose: hand-picking which competency
questions to write would let the author choose the ones that match, which
measures the author.

The control is what makes the numbers readable: how similar the corpus's own
questions are to each other within a category. Without it a similarity has no
scale.

| Category | Similarity to FIBO's questions | Control, corpus to itself |
|---|---:|---:|
| Shareholder return | 0.721 | 0.816 |
| Financials | 0.669 | 0.803 |
| Accounting | 0.663 | 0.790 |
| Legal | 0.655 | 0.792 |
| Footnotes | 0.647 | 0.792 |
| Company overview | 0.615 | 0.777 |
| Governance | 0.615 | 0.786 |
| **Risk** | **0.592** | **0.831** |

FIBO reaches about four fifths of the similarity two real questions have to each
other. That is the answer to the question this section asks.

Risk is the exception and the shape of the exception is clean: the lowest
similarity to FIBO and the highest internal control. A category whose questions
are highly similar to each other and least similar to the ontology is a coherent
topic the ontology does not cover. It is scoped out rather than counted as a
failure.

## FIBO's chosen names are not the words the filings use

FIBO declares a synonym layer, and it is thinly filled: of 1,325 classes in
scope, 231 carry any annotation at all — 0.1743.

Where it is filled, it matters. Of 312 alias pairs, 279 have counts that can be
trusted; the other 33 are single common words whose totals absorb compounds with
different meanings and are excluded. Among the trusted pairs the alias appears
in more filings than FIBO's own label in 43 cases, and in 31 the formal label
never appears at all while the alias does.

This is a register difference and not a coverage gap. FIBO declares those terms
itself. It labels definitionally and jurisdiction-neutrally; filings use market
convention. That is why the synonym layer had to be a separate experimental
condition rather than folded into the ontology.

## Reasoning adds structure the class list does not carry

The extraction prompt receives a flat list of class names. An OWL 2 RL closure
over the 70 classes the FIBO condition ships more than doubles the classes that
have a parent inside that set, from 7 to 15, and takes relations with both
endpoints resolved from 4 to 28. It adds 84 subclass edges between scoped
classes and 70 equivalences.

Every count here is a floor. HermiT and Pellet need a JVM this machine does not
have, so the engine is a pure-Python OWL 2 RL closure, and RL does not derive
subsumption from complex class expressions. A complete reasoner would find
strictly more.

## What this does not establish

Similarity of phrasing and topic, not of answerability. A high score means FIBO
asks this kind of question, not that a graph built from it can answer one. That
is section 1.3's question and section 1.4's answer.
