# LLM-as-a-judge protocol for FinDER mixed-query annotation

This protocol is the replacement for an unavailable human annotation round. It
does not create human labels. The paper must call its output a calibrated
LLM-judge or expert-proxy panel result.

## Why this is defensible, and where it is not

FinanceBench uses expert-written financial questions, answers, and evidence,
then manually reviews generated answers. FinDER provides the same useful
source-grounding structure, but it does not label whether a newly composed
cross-category question genuinely requires two organizational views.

JUDGE-BENCH reports substantial variation in LLM–human agreement by task,
judge model, and evaluator expertise. JETTS similarly treats judge quality as
an object of evaluation rather than assuming that a judge is correct.
Prometheus 2 demonstrates that a trained evaluator can be useful, but its
agreement numbers are not a guarantee for this financial routing task.

Therefore this panel is suitable for a bounded, reproducible screening and
uncertainty analysis. It is not evidence of human inter-annotator agreement and
cannot by itself justify the phrase “finance experts judged.”

## Panel roles

Each item is independently sent to five heterogeneous roles. The role prompt
contains the same evidence packet and the same JSON schema; only the review
lens changes.

1. Financial reporting reviewer: ASC/IFRS framing, metric, period, unit,
   accounting basis, and whether the proposed statement is supported by filing
   evidence.
2. Equity-research reviewer: whether the combined question corresponds to a
   decision a financial analyst would actually ask, without inventing causal
   implications.
3. Graph and multi-agent reviewer: whether both category views are necessary,
   whether one view is sufficient, and whether the proposed coalition is the
   minimum authorized team.
4. Governance and security reviewer: provenance, isolation, protected fields,
   conflict handling, and whether the answer should abstain.
5. Benchmark-statistics reviewer: leakage, slot atomicity, alternative
   explanations, positional bias, and whether the label is identifiable from
   the supplied evidence.

The panel does not expose model answers, routing scores, policy decisions, or
the author's preferred label. Candidate order and answer-arm order are
randomized independently for every judge.

## Required output

Each judge returns JSON only:

```json
{
  "query_class": "local|complementary|conflict|unanswerable|uncertain",
  "both_views_required": "yes|no|uncertain",
  "single_view_sufficient": "yes|no|uncertain",
  "financially_natural": "yes|no|uncertain",
  "slot_1_valid": "yes|no|uncertain",
  "slot_2_valid": "yes|no|uncertain",
  "conflict_is_comparable": "yes|no|uncertain",
  "protected_evidence_present": "yes|no",
  "decision": "accept|reject|abstain",
  "rationale": "short evidence-linked explanation"
}
```

Judges must cite packet evidence spans or graph fact identifiers in the
rationale. They may not fill missing facts from general financial knowledge.

## Aggregation and reporting

The primary label is a strict majority of the five roles. A case is `abstain`
when no label has at least three votes, or when either atomic slot fails the
validity rule. The minimum-view label is accepted only when at least three
roles say `both_views_required=yes` and at least three say
`single_view_sufficient=no`. Otherwise the case is unresolved.

Report, per dimension:

- vote distribution and abstention rate;
- pairwise agreement and Krippendorff's alpha between judge roles;
- leave-one-role-out majority stability;
- paraphrase and left/right evidence-order stability;
- disagreement cases with the full anonymized rationale;
- the number of cases excluded before answer evaluation.

The answer experiment uses only accepted cases. Unresolved cases are not
silently assigned to a single-view or coalition class. This avoids turning
judge disagreement into apparent routing failure or apparent multi-agent
benefit.

## Calibration without new human labels

Calibration uses three answer-blind control groups already available in the
repository:

- FinDER-native single-category questions, where the source category is known;
- synthetic comparable-fact conflicts, where the minimum verification team is
  known by construction;
- protected-field injections, where disclosure is an unambiguous failure.

The panel is not tuned on the revised 13-case outcome. Calibration statistics
are reported separately from the mixed-query result. A judge panel that fails a
control is marked unreliable for that dimension and its labels are not used for
the corresponding claim.

## Claim boundary for the paper

The strongest permitted wording is:

> A heterogeneous, blinded LLM-judge panel provides reproducible proxy labels
> for mixed-query necessity and financial naturalness; these labels are
> calibrated against FinDER-native and synthetic graph controls but are not
> human annotations.

The paper must not report panel agreement as human agreement, call the roles
licensed finance experts, or use panel consensus to prove that SDCR improves
real-world financial answer quality.

## References motivating the protocol

- FinanceBench: <https://arxiv.org/abs/2311.11944>
- JUDGE-BENCH: <https://arxiv.org/abs/2406.18403>
- JETTS (ICML 2025): <https://proceedings.mlr.press/v267/zhou25af.html>
- Prometheus 2: <https://aclanthology.org/2024.emnlp-main.248/>
- LLM-as-a-judge survey: <https://aclanthology.org/2025.emnlp-main.138/>

---

# Addendum (2026-08-05) — answer-grading panel for the an1/an2 experiments

The protocol above governs mixed-query annotation. This addendum governs the
registered secondary metric of the answering experiments: grading candidate
answers against FinDER gold answers. It follows the practices the
LLM-as-judge literature has converged on — reference-based pointwise grading
with the rubric in the prompt (Prometheus), structured reasoning before the
verdict (G-Eval), small discrete verdict scale, explicit verbosity-bias
control, and calibration of the judge itself before trusting it
(JUDGE-BENCH/JETTS) — and it fixes one defect found before any judgment ran.

## Panel composition — fixed before the panel runs

The registration said "three MARA-hosted judges". MARA hosts only the three
answerer models (plus a same-family DeepSeek-V3.2), so that panel would have
graded its own answers — self-enhancement bias by construction. Fixed by
cross-judging: each answer is graded by the two MARA models that did not
write it, plus Kimi (Moonshot; `KIMI_API_KEY`), which contributed no
extraction and no answer anywhere in this study. Within one answerer's lane
the judge mix is constant, so the registered within-model condition
contrasts are unconfounded. Ties across the three judges are reported as
`unresolved`, never folded into a verdict.

## What the judge sees, and does not

Sees: the question, the gold answer, one candidate answer. Does not see:
condition label, model name, tag, or any other candidate. Citation markers
(`[p0@656]`) are stripped from candidates before grading — they would leak
the condition.

## Grading prompt (frozen; hash recorded in the run config)

    You grade one candidate answer against a gold answer for a question
    about SEC filings. The gold answer is authoritative: do not overrule
    it with your own financial knowledge, even if you believe it is wrong.

    Follow these steps in order:
    1. From the gold answer, list the facts the question requires.
    2. For each required fact, check the candidate: match, missing, or
       wrong. Figures match when equal within 1% after converting units
       and scale words ($1.9 billion = $1,906,715 thousand = matching).
    3. Only then choose the verdict, by this rubric:
       - correct: every required fact matches
       - partially_correct: at least one required fact matches and at
         least one is missing or wrong
       - incorrect: the candidate contradicts the gold answer, or no
         required fact matches
       - abstained: the candidate declines to answer
    Length is not quality. Extra detail the question did not ask for
    neither helps nor hurts, unless it contradicts the gold answer.

    Return JSON only:
    {"required_facts": [{"fact": "...", "in_candidate": "match|missing|wrong"}],
     "verdict": "correct|partially_correct|incorrect|abstained",
     "rationale": "one sentence naming the decisive match or mismatch"}

## Calibration before any real grading — a judge must earn its votes

Four control groups, verdicts known by construction; a judge failing any
control is excluded and the exclusion reported:

    gold answer submitted as the candidate        -> correct
    another case's answer swapped in              -> incorrect
    gold with every figure corrupted x1000        -> incorrect or partial
    "cannot determine"                            -> abstained

## Reporting

Per condition and model: verdict distribution with `unresolved` separate;
pairwise judge agreement (Cohen's kappa) and whether Kimi is an outlier;
leave-one-judge-out majority stability; calibration results. Temperature 0
everywhere; every judgment persisted per (case, condition, model, judge).

### Panel composition amendment (2026-08-06, before any panel verdict)

Kimi's quota did not recover within a 13-hour probe window (exhausted by the
MA adjudication run), so the third seat is unavailable before the deadline.
The panel proceeds as MARA-only cross-judging: every answer is graded by the
two MARA models that did not write it, both of which passed all four
calibration controls (40/40 under prompt v2 with figure-dense controls);
DeepSeek's seat is admitted only if it passes the same calibration on its
quota day. Two-judge ties are reported as `unresolved`, never resolved by
fiat. If Kimi's quota returns before the build, its calibrated seat is added
and reported as the third vote.
