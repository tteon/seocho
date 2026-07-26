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
