# ADR-0099: AnswerShape + RouteProfile — Opik-Grounded Answer-Path Design

Date: 2026-06-03
Status: Proposed

## Context

Three Comet/Opik projects from prior research runs were analysed to find
designs worth porting into SEOCHO (workspace `tteon`, read via the opik
MCP / SDK):

- **`icml2026_kg_agent`** — `fibo_ground_node_label` / `fibo_ground_edge_type`
  traces: embedding-similarity grounding of a natural-language intent to
  FIBO node labels / edge types, returning ranked `(type, score)` above a
  threshold (e.g. `audit committee` → `[["hasCommittee",0.615],
  ["HAS_COMMITTEE",0.546],["OVERSEES",0.414]]`).
- **`icml2026_kg_agent_presentation`** — `exp5_policy_compare` (200 traces):
  `single_call` vs `planner` policy, token-F1 per question, bucketed
  ambiguous / compliance / factual / multi-hop (50 each).
- **`kdd2026-live-policy-traces`** — `semantic_graph` agent over 147
  `task_run`s. Each task carries a `semantic.route_profile` span and a
  route taxonomy: R1_LOOKUP / R2_STRUCTURED_JOIN / R3_RULE_GROUNDED /
  R4_GRAPH_JOIN (workhorse, 81/147) / R5_LONG_CONTEXT_REASONING /
  R6_DOC_ONLY_REFERENCE, each mapped to a `tool_policy`, `planner`, and
  `determinism` mode. `build_semantic_intent_context` classifies an
  `answer_shape` (e.g. `scalar_metric`) **before** retrieval.

Two transferable contracts emerged, matching the dataclasses the team had
been prototyping (`AnswerShape`, `RouteProfile`):

1. **AnswerShape** — classify the expected answer shape, steer synthesis.
2. **RouteProfile** — route-conditional `tool_policy` + `planner`.

The decisive empirical signal from `exp5_policy_compare` (token-F1 by
bucket × policy):

| bucket     | single_call | planner | winner          |
|------------|-------------|---------|-----------------|
| ambiguous  | 0.272       | 0.217   | single_call     |
| factual    | 0.334       | 0.291   | single_call     |
| compliance | 0.286       | 0.280   | single_call     |
| multi-hop  | 0.295       | **0.342** | **planner +0.048** |

The multi-step `planner` only beats `single_call` on multi-hop; on every
other bucket it is equal-or-worse **and** more expensive. The design
lesson is route-conditionality: never pay for the planner unless the
question is multi-hop.

## Decision

Land both contracts as minimal, additive, env-gated layers over the
existing query lane (`RoutingPolicy.decide` → backends; these add the
execution-strategy layer the lane previously left implicit). Validate
each with a controlled A/B on the FinDER tutorial subset (DozerDB +
MARA/MiniMax-M2.5), scored with `benchmarking.compare_answers`
(exact/contains) + `eval.gopts_answer_quality.token_f1` (Layer-3, F3).

### 1. AnswerShape (`seocho/query/answer_shape.py`)

`AnswerShape` enum (`scalar_metric`, `entity_name`, `entity_list`,
`location`, `explanation`, `unknown`); rule-based
`classify_answer_shape(question)` (deterministic, `UNKNOWN` when no rule
fires); `terse_directive(shape)` returning a "answer with ONLY the
value/name/…" instruction for value-shapes and `None` for
explanation/unknown (prose stays correct). `QueryAnswerSynthesizer.synthesize`
gains an optional `answer_shape` param (additive; `None` = baseline).
`local_engine.ask` classifies + passes it only under `SEOCHO_ANSWER_SHAPE`.

Two-tier classification (rules now, LLM fallback later) was agreed; the
rule tier covers the FinDER bucket so the LLM tier is deferred.

### 2. RouteProfile (`seocho/query/route_profile.py`)

`ToolPolicy` / `Planner` enums, `RouteProfile` dataclass, `ROUTE_CATALOG`
(`lookup`, `entity_summary`, `graph_join`, `multi_hop`). **`multi_hop` is
the only route that uses `Planner.MULTI_STEP`** — the exp5 rule encoded
directly. `classify_route_class(question, reasoning_type)` lets a curated
dataset label (FinDER `single_hop`/`numeric_lookup`→`lookup`,
`compositional`→`multi_hop`) win over keyword rules.
`planner_exec_params` maps `MULTI_STEP`→`reasoning_mode + repair_budget=2`,
`TEMPLATE`→single pass. `local_engine.ask` applies it under
`SEOCHO_ROUTE_PROFILE`.

### A/B evidence

AnswerShape — first the 5 scalar/name/location cases (off→on):

| metric      | baseline | treatment |
|-------------|----------|-----------|
| exact_match | 0.00     | **1.00**  |
| token_f1    | 0.18     | **1.00**  |
| contains    | 1.00     | 1.00      |
| mean_ask_ms | 2720     | 2639      |

Then the wider 10-case set (all reasoning types incl. compositional):

| metric      | baseline | treatment |
|-------------|----------|-----------|
| exact_match | 0.00     | **0.60**  |
| token_f1    | 0.146    | **0.629** |

token_f1 by reasoning_type (baseline → treatment):
`single_hop 0.15→0.61`, `numeric_lookup 0.26→1.00`,
`compositional 0.075→0.50`. Shapes assigned: 5 scalar_metric, 2
entity_name, 1 location, **2 unknown** (no directive → baseline prose,
zero regression). The win generalises across every classified bucket
incl. compositional; the ceiling below 1.0 is bounded by ~3 cases where
retrieval itself returns empty (ontology/extraction-fit), which a terse
synthesis directive cannot rescue — not an AnswerShape limitation.

RouteProfile (10-case set, AnswerShape off to isolate):

| metric          | baseline | treatment |
|-----------------|----------|-----------|
| token_f1        | 0.216    | 0.202     |
| contains        | 0.70     | 0.70      |
| mean_ask_ms     | 2674     | 2620      |
| compositional_f1| 0.107    | 0.111     |
| simple_f1       | 0.243    | 0.224     |

## Consequences

Positive:

- **AnswerShape is a large, cheap, measured win** (+0.82 token_f1, 0→1.0
  exact) with no retrieval change and no latency cost. It targets the real
  FinDER gap: the lane retrieves the right fact (contains=1.0) but wraps it
  in prose. AnswerShape steers a terse value answer.
- Both layers are additive and env-gated; default-off means zero impact
  on existing callers. Each has unit tests (11 + 11) and a documented A/B.
- The contracts give the implicit `tool_policy`/`planner`/`answer_shape`
  that were scattered across the agent prompt, `semantic_flow`, and the
  graph_cot lane a single declarative home.

Negative / honest findings:

- **RouteProfile's planner axis showed no measurable effect** on this
  workload. `MULTI_STEP` is wired to `reasoning_mode`/`repair_budget`, and
  the repair loop only fires on empty retrieval — the FinDER cases either
  succeed first try or fail on ontology/extraction mismatch that repair
  cannot fix, so planner depth barely changes execution. This mirrors
  exp5's own finding (planner helps only narrow multi-hop) and pinpoints
  that seocho's FinDER bottleneck is **synthesis precision (AnswerShape)
  and extraction/ontology-fit**, not planner depth.
- RouteProfile therefore lands as the contract scaffold + the negative
  measurement that scopes follow-up: its planner axis needs a real
  multi-plan / decomposition executor (the deferred GOPTS F8) to have
  teeth, or `tool_policy` must be wired to an effective lever (e.g.
  graph_join vector fan-out).

Deferred:

- LLM tier of `classify_answer_shape` (rules suffice for FinDER).
- Scored ontology grounding (the icml `fibo_ground_*` pattern) as a
  retrieval leg — not yet ported.
- `SEOCHO_ANSWER_SHAPE` is **default-on (opt-out via =0)**. See the
  correction below for what that default actually buys.

## Correction (2026-06-03, cross-model LLM-judge)

A cold multi-expert review challenged the AnswerShape "win" as
token-F1 / exact-match *metric-gaming* (terse answers trivially maximize
overlap against short gold strings; the baseline already had
contains-match = 1.0, i.e. the correct fact was retrieved and stated,
just wrapped in prose). We re-measured with a **cross-model LLM-judge**
(generator MiniMax-M2.5, judge DeepSeek-V3.1) on the 10-case FinDER set:

| metric              | AnswerShape off | AnswerShape on |
|---------------------|-----------------|----------------|
| token_f1            | 0.16            | 0.41           |
| exact_match         | 0.00            | 0.30           |
| **LLM-judge score** | **0.70**        | **0.70**       |

**The judge sees ZERO quality difference (+0.000).** The token-F1/exact
gains are a metric artifact, not a correctness gain — the judge scores
the verbose baseline answer ("…is $211.9 billion") exactly as correct as
the terse one. The review was right.

**Reframing:** AnswerShape is a **format / extractive-consumer feature,
not an answer-quality improvement.** It is genuinely useful for
programmatic consumers (tool-call chaining, structured extraction, and
any token-F1/exact-match leaderboard), and the judge shows it is
**quality-neutral** (no regression). It stays default-on on that basis —
terse value answers are a reasonable default for a graph-memory
middleware whose outputs are often consumed programmatically — but it
must NOT be cited as a quality win. The earlier "+0.48 f1 measured win"
framing is withdrawn.

**Answerability** (the upstream gate the review flagged) measured ~0.80
on this ontology (≈20% of cases retrieve 0 records) — a real but
smaller-than-feared bottleneck, and the genuine next target. (The
text-based answerability proxy used here is approximate; a record-count
instrument is the follow-up.)

Net honest conclusion across all four opik-derived legs: **none produced
a judge-confirmed answer-quality improvement.** AnswerShape is a
quality-neutral format win; RouteProfile / F8 / grounding are null. The
session's real value is the *instrumentation* (Layer-1/2/3 + answerability
+ cross-model judge) that produced these honest negatives — and the
redirection they imply: attack answerability and judge-scored quality,
not lexical metrics.

## Implementation Notes

- new: `src/seocho/query/answer_shape.py`, `src/seocho/query/route_profile.py`
- touched: `src/seocho/query/answering.py` (`synthesize(answer_shape=...)`),
  `src/seocho/local_engine.py` (env-gated wiring for both)
- tests: `tests/seocho/test_answer_shape.py` (11),
  `tests/seocho/test_route_profile.py` (11)
- commits: AnswerShape `b129f26`, RouteProfile `0c6865c`; MARA provider
  preset (`c58eaca`) unblocked the live e2e since the repo's other LLM
  keys were invalid.
- harnesses: FinDER subset via `examples/finder/datasets/finder_tutorial_subset.json`,
  scored with `seocho.benchmarking` + `seocho.eval.gopts_answer_quality`.
- env switches: `SEOCHO_ANSWER_SHAPE`, `SEOCHO_ROUTE_PROFILE` (both
  default-off).
- aligns with the codebase-change first principle (test + CI + stated
  advantage + experiment) and CLAUDE.md §9 (terse answers still trace
  workspace_id + routing metadata).
- relates to: ADR-0097 (GOPTS cost-ranked emission; RouteProfile's
  `cost_ranked` planner reuses it), ADR-0091 (QueryEnrichmentRouter —
  RouteProfile is the execution-strategy layer downstream of
  `decide()`), ADR-0095 (graph_cot lane — exempt from both env gates).
