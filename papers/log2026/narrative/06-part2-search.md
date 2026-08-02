---
draws_on:
  []
---
# Part 2 — how the search results will be judged

Part 1 asks whether an ontology helps *build* the graph. Part 2 asks whether it
helps *answer* with one, and whether letting several models divide the work
helps further. The dataset's own answer is the target.

This document fixes the scoring before the runs, so no metric can be chosen
after seeing which one is kind to us.

---

## 1. What has to be shown, in order

1. The models cannot already answer from memory. If they can, nothing that
   follows is about retrieval.
2. Retrieving something beats retrieving nothing.
3. Retrieving from a graph beats retrieving passages.
4. A graph built with an ontology beats one built without.
5. Choosing how the work is organised — who is asked, in what order — beats a
   single straight-through pass.

Each step is only worth running if the one before it held. A paper that skips
step 1 and reports step 4 has measured nothing.

---

## 2. Conditions

**Where the evidence comes from.** One factor, five levels.

| Level | What the model sees | Why it is here |
|---|---|---|
| closed book | the question only | Contamination control. FinDER comes from public filings the models may have memorised. If this scores well, the rest is theatre. |
| passages | the case's own reference text, retrieved by embedding | The comparison every reviewer will ask for. A graph that cannot beat plain retrieval has no claim. |
| graph, no ontology | the subgraph built under the no-schema condition | Isolates the graph structure from the vocabulary |
| graph + ontology | the subgraph built under the FIBO condition | The question Part 1 leaves open |
| passages + graph | both | Whether the two are complementary or redundant |

**How the work is organised.** A second factor, and deliberately *not* crossed
with the first at full width — five levels times four organisations times three
models is sixty cells and the ontology effect would be unreadable inside it.

The evidence factor is swept with the organisation held at the simplest. Then
the winning evidence level is fixed and the organisation is swept. Interaction
is only examined if a pilot shows one.

| Organisation | What it does |
|---|---|
| straight through | retrieve once, answer once. The floor, and the cheapest |
| sort first | classify the question — a lookup, an arithmetic comparison, a multi-step trace — and pick the retrieval to match |
| answer then check | answer, verify each claim against the retrieved evidence, revise. This is the one the verification thesis predicts should win |
| split and gather | decompose a multi-part question, send parts to different views, combine |
| free agent | a model with a query tool and a loop, no fixed path. An **upper bound**, run once, not a candidate |

**Which model.** DeepSeek-V3.1, gpt-oss-120b, MiniMax-M2.7, each running every
condition, so a model effect cannot be mistaken for a condition effect.

---

## 3. Scoring

No single number. Four tiers, and the tier a number sits in determines how much
weight it can carry.

### Primary — numeric accuracy, no judge involved

Most FinDER answers contain a figure, and a figure is either right or wrong.
Extract every number from the gold answer and from the prediction, apply scale
words so "59.4" and "59.4 million" are different, and match within 1%.

    numeric accuracy = share of gold figures the answer states correctly
    numeric precision = share of stated figures that are correct

Both are reported. Accuracy alone rewards an answer that lists every plausible
number; precision alone rewards saying almost nothing.

This is primary because it is reproducible by anyone, needs no model, and cannot
be argued with. It applies to the subset of cases whose gold answer contains a
figure, and that subset size is reported every time.

### Secondary — a judge panel, for the answers that are prose

Some answers are explanations, and no arithmetic reaches them. Those need a
judge, and a judge needs its own controls, because a single judge has already
been shown unreliable on this project: agreement between judges ran between 0.20
and 0.67, and one model was a consistently lenient outlier.

The protocol:

- **Three judges, majority verdict.** Never one.
- **No self-judging.** A model never scores an answer it produced, nor one
  produced by a sibling of the same family. Self-preference is the best
  documented bias in this setup and it is free to avoid.
- **The rubric is a checklist, not a rating.** "Does the answer state the figure
  the gold answer states / contradict it / omit it" beats "rate 1-5", because
  the checklist is auditable and the rating is a vibe.
- **Order and length are neutralised.** Gold and prediction appear in randomised
  order; length is capped so a longer answer cannot win on volume.
- **Agreement is published.** Cohen's kappa between every judge pair goes in the
  paper. A result resting on a panel that does not agree with itself is not a
  result.
- **Judges are held fixed across conditions.** Changing the panel between
  conditions would make the comparison meaningless.

### Supporting — reported, never load-bearing

| Measure | What it is good for | Why it cannot be primary |
|---|---|---|
| exact match | free, unambiguous | Near zero on free-form answers; punishes correct paraphrase |
| token overlap (F1) | catches partial credit | Rewards verbosity, blind to a wrong number |
| semantic similarity | catches correct paraphrase | **Blind to the thing that matters most here.** "$59.4 million" and "$594 million" are almost identical to an embedding and one of them is wrong. Reported to show it does not track correctness, not to claim quality. |

That last row is worth stating explicitly in the paper. A financial QA result
carried by embedding similarity is a result about phrasing.

### Guardrails — reported for every condition, no exceptions

| Measure | Why a reviewer will want it |
|---|---|
| answered rate | A system that abstains when it lacks evidence is behaving correctly. Scoring it beside one that guesses, without showing how often each declined, hides the difference. |
| tokens per answer | The argument for a graph is that joins and aggregation are cheap and do not need a model at scale. If the graph condition costs three times the tokens, that argument is gone, and only this number shows it. |
| wall-clock per answer | Same reason, from the user's side |
| evidence found rate | How often retrieval returned anything at all. A condition that fails to retrieve and then answers from memory is being scored on the wrong thing. |
| attribution rate | Share of the answer's claims traceable to a retrieved item. This is the measure the verification thesis actually rests on, and it is the one a graph should win even where it loses on accuracy. |

---

## 4. What reviewers will attack, and the answer

**"The models memorised the filings."**
The closed-book condition is the first row of every table. If it is high, we say
so and scope the claim to the cases where it is not.

**"You did not compare against plain retrieval."**
Passages is a condition, not an afterthought, and it runs on the same cases with
the same judge.

**"Your judge is the same model you are evaluating."**
No model judges its own family. The exclusion is stated per condition.

**"The judge is not calibrated."**
Pairwise kappa is published. Where the panel disagrees, the case is reported as
disputed rather than resolved by a tiebreak nobody can audit.

**"Semantic similarity is not accuracy."**
Agreed, and demonstrated: the paper shows a case where similarity is high and
the figure is wrong. Similarity is never the headline.

**"Sixteen cases."**
Part 1 used sixteen because extraction is expensive per case. Part 2 is a
retrieval and answering comparison, so the sample is drawn to give each category
enough cases for the primary metric to have an interval. The number is fixed
before the run and the interval is computed by resampling cases, as in Part 1.

**"You tuned the prompt per condition."**
One answering prompt, identical across conditions except for the evidence block.
The prompt is hashed into the run fingerprint.

---

## 5. Reporting rules

- Every table reports attempted against scored. A failure is a failure, never an
  imputed score.
- Every difference carries an interval from resampling cases. A difference whose
  interval crosses zero is described as not separated, never as a trend.
- All conditions are reported every time, including the ones that lose.
- The direction expected for each step in section 1 is written down before the
  run and reported against, including where it was wrong.
- Cost is reported beside quality in the same table, not in an appendix.
