# Opik → SEOCHO Answer-Path Validation

Reproducible evidence for ADR-0099 / ADR-0100 / ADR-0101. Three Comet/Opik
research projects (`icml2026_kg_agent`, `icml2026_kg_agent_presentation`,
`kdd2026-live-policy-traces`, workspace `tteon`) were analysed to find
answer-path designs worth porting into SEOCHO. Four legs were implemented
and A/B-validated against the FinDER tutorial subset
(`examples/finder/datasets/finder_tutorial_subset.json`, 10 synthetic
10-K cases) on a live DozerDB graph with the MARA / MiniMax-M2.5 backend,
scored with `seocho.benchmarking.compare_answers` (exact / contains) and
`seocho.eval.gopts_answer_quality.token_f1`.

## Why (rationale)

The opik `exp5_policy_compare` run (200 traces) showed a multi-step
`planner` only beats `single_call` on the multi-hop bucket; on
ambiguous / factual / compliance it is equal-or-worse **and** costlier.
The `build_semantic_intent_context` traces classify an `answer_shape`
before retrieval; the `semantic.route_profile` spans map each question to
a `(tool_policy, planner, determinism)` tuple; the `fibo_ground_*` traces
ground NL terms to ontology types by scored similarity. The hypothesis
set below is each design's expected effect on SEOCHO's lane.

## Legs, hypotheses, and measured results

| leg | hypothesis (expected effect) | result | decision |
|-----|------------------------------|--------|----------|
| **AnswerShape** | classify the answer shape and emit a terse value → lift exact/token-F1 without changing retrieval | **token_f1 0.146→0.629, exact 0→0.60** (wide 10-case); 5-case scalar set 0→1.0 | **default-on** (earned) |
| **RouteProfile** | route-conditional planner (multi_step only for multi-hop) → cheaper on simple, better on multi-hop | f1 0.216→0.202, latency flat — **null** | default-off (scaffold for F8) |
| **F8 multi-plan** | execute top-K shapes + RRF fuse → lift recall on compositional | mechanism works (009: 0→2 records) but **f1 null** — bottleneck is extraction/ontology-fit, not shape | default-off |
| **Scored grounding (lexical)** | semantic synonym→ontology-type match → more non-empty retrievals | isolated 2/5→4/5 correct; **FinDER e2e null** (contains/exact/f1 unchanged) | default-off (no e2e win) |
| **Scored grounding (embedding scorer)** | embeddings fix the non-lexical synonym miss lexical made | isolated 4/5 (fixes `location`, breaks `leadership` — no domination); **FinDER e2e null** (0.569→0.569) | lexical stays default; embedding opt-in |

## Verification

- Every leg ships unit tests (AnswerShape 11, RouteProfile 11, multi-plan
  10, grounding 9) and passes `bash scripts/ci/run_basic_ci.sh` (388).
- Each A/B is a controlled off-vs-on run on the same cases; AnswerShape
  held constant when isolating other legs. Raw aggregates are the data
  files in this directory:
  - `answershape_wide.json` — AnswerShape off/on, per-reasoning-type f1.
  - `t2_baseline.json` — the pre-AnswerShape 5-case baseline.
  - `routeprofile_ab_baseline.json` / `routeprofile_ab_treatment.json`.
  - `grounding_e2e_summary.txt` — grounding off/on, per-case contains/f1.

## Conclusion (what others should take away)

Of the four opik-derived mechanisms, **only AnswerShape produced a
measured end-to-end win** on this workload, so only it is defaulted on
(opt-out via `SEOCHO_ANSWER_SHAPE=0`). RouteProfile, F8 multi-plan, and
scored grounding are correct, unit-proven mechanisms kept **opt-in**
because the FinDER + MARA workload does not exercise their value path —
defaulting them on would be a flip without evidence (CLAUDE.md §20). The
honest, data-grounded takeaway: SEOCHO's measurable FinDER quality lever
is **synthesis precision**; the execution-diversity and ontology-fit
mechanisms await a workload (or a live embedding backend) that activates
them. This matches exp5's own finding that planner depth only helps
multi-hop.
