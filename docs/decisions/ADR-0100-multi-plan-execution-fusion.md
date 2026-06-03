# ADR-0100: Multi-Plan Execution + Result Fusion

Date: 2026-06-03
Status: Proposed

## Context

ADR-0097 (GOPTS) enumerates K candidate Cypher plans and cost-ranks them
but emits **only the top-1** — multi-plan execution was explicitly
deferred. ADR-0099's RouteProfile A/B then produced a decisive null
result: the `multi_hop` route's `MULTI_STEP` planner, wired to
`reasoning_mode`/`repair_budget`, showed no measurable effect because the
repair loop only fires on empty retrieval. The planner axis has no teeth
because there is no executor that actually runs more than one plan.

The opik exp5 evidence says multi-step planning only helps **multi-hop**
questions (+0.048 f1; every other bucket equal-or-worse). The FinDER T2
baseline showed the lane's compositional cases (`finder_tut_006`,
`finder_tut_010`) under-retrieve — a single query shape misses evidence a
different shape would surface. That is exactly what multi-plan execution
+ fusion addresses: run several query shapes, fuse their results, lift
recall on under-specified / multi-hop questions.

Preconditions from the F8 ticket are met: ADR-0097 (enumeration +
cost_model), F2 (Layer-2 latency harness), F3 (Layer-3 answer quality)
all landed. RRF fusion already exists (`seocho/agent/fusion.py`,
ADR-0091) and is reused rather than reinvented.

## Decision

Add a multi-plan executor that, for questions the RouteProfile routes to
`multi_hop`, builds the top-K candidate Cypher plans, executes each
read-only against the workspace, and fuses the record sets with
Reciprocal Rank Fusion. Strictly gated and route-scoped so the
single-plan path is unchanged everywhere else.

### Executor (`seocho/query/multi_plan.py`)

`execute_multi_plan(*, builder, executor, question, intent_data, shapes,
workspace_id, limit, rrf_k=60) -> MultiPlanResult`:

1. For each `shape` in `shapes` (a small ordered set of cypher_shapes,
   e.g. `("relationship_lookup", "neighbors", "entity_lookup")` for a
   multi-hop question), build a plan via `CypherBuilder.build(intent=shape,
   **intent_data)` and execute it via `GraphQueryExecutor`.
2. Collect each plan's non-empty records as a named ranked list.
3. Fuse with `ReciprocalRankFusion(k=rrf_k)` (uniform weights) → a single
   deduped, rank-ordered record list (identity from `id`/`elementId`).
4. Return `MultiPlanResult(records, plan_provenance)` where provenance
   records, per shape: the cypher, row count, and contribution — emitted
   to the trace for auditability (CLAUDE.md §9).

Plans that error or return empty are dropped (best-effort), never failing
the whole call. When only one plan yields records, fusion is a no-op pass
-through (identical to single-plan).

### Gating

`local_engine.ask` runs the multi-plan path only when **both**:
- `SEOCHO_MULTI_PLAN` is enabled (default-off — this is a recall/latency
  trade still being validated, unlike AnswerShape which is proven), and
- the question's RouteProfile `route_class == "multi_hop"`.

For every other route, or with the env unset, execution is the existing
single top-1 plan. This honours exp5: only multi-hop pays the K-plan cost.

### Shapes

The candidate shapes for `multi_hop` default to
`("relationship_lookup", "neighbors", "entity_lookup")` — the three that
can surface cross-entity evidence — capped by
`RoutingPolicy.thresholds["plan_candidates"]` (the GOPTS K, default 4).
Cost-ranking from ADR-0097 orders them; fusion makes the order
non-critical (RRF rewards agreement across shapes).

## Consequences

Positive:
- Gives the RouteProfile `MULTI_STEP` planner real teeth: multi_hop
  questions now execute several query shapes and fuse, instead of a no-op
  repair toggle.
- Reuses the existing RRF fusion and GOPTS enumeration — small new
  surface (one module).
- Strictly route-scoped + env-gated: zero impact on the proven
  single-plan + AnswerShape path.

Tradeoffs:
- K-plan execution multiplies graph round-trips for multi-hop questions
  (mitigated: K small, route-scoped, env-gated; F2 harness measures the
  latency cost). Each extra plan is a read-only Cypher execution, not an
  LLM call — cost is DB time, not tokens.
- Fusion improves recall but can dilute precision if a noisy plan
  contributes off-target rows; RRF's rank weighting and the synthesis
  step (+ AnswerShape) mitigate this. The A/B on compositional cases is
  the honest check.
- If the multi-hop bottleneck is extraction/ontology-fit (not query
  shape), multi-plan won't help — the A/B will say so, and the result is
  scoped accordingly (a null here points at extraction, not fusion).

## Implementation Notes

- new: `src/seocho/query/multi_plan.py`; reuses
  `seocho/agent/fusion.py:ReciprocalRankFusion`,
  `seocho/query/executor.py:GraphQueryExecutor`,
  `seocho/query/cypher_builder.py:CypherBuilder`,
  `seocho/query/route_profile.py` (route gate).
- touched: `src/seocho/local_engine.py` (env+route-gated multi-plan
  branch before single-plan execution).
- tests: `tests/seocho/test_multi_plan.py` (fusion math, empty/one-plan
  pass-through, provenance, drop-on-error).
- env switch: `SEOCHO_MULTI_PLAN` (default-off).
- experiment: A/B on the FinDER compositional cases (006/010), single-
  plan vs multi-plan, scored with `benchmarking.compare_answers` +
  `eval.gopts_answer_quality.token_f1`.
- relates to: ADR-0097 (enumeration/cost_model; this consumes its K),
  ADR-0099 (RouteProfile multi_hop gate), ADR-0091 (RRF fusion reuse).
