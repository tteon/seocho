# ADR-0097: GOPTS Cost-Ranked Cypher Emission

Date: 2026-05-25
Status: Proposed

## Context

ADR-0090 introduced tiered NL→Cypher with `cypher_template_lookup`,
`similar_query_search`, `schema_introspect`, and `validate_cypher` tools
inside `QueryAgent`. ADR-0091 added the `QueryEnrichmentRouter` that
fans out across Cypher / Vector / Fulltext / GDS backends and fuses with
RRF. ADR-0092 made the LPG property surface agent-readable
(`answers`, `useWhen`, `embeddingText`, `semanticRole`,
`preferredNextRelations`). ADR-0095 fixed the Graph-CoT lane contract.
All four are implemented (`seocho/agent/enrichment_router.py`,
`seocho/index/property_shaper.py`, `seocho/tools.py`).

Inside the Cypher backend of that lane, however, generation still emits
**a single plan per question**: `CypherBuilder.build` returns one
parameterized template selected by intent + labels, with no
cost-awareness against the live workspace's index population. This is
the GOPTS (VLDB 2024 LSGDA) gap: when multiple plans are valid, none is
ranked; when the chosen plan is index-blind, it pays a full-scan cost
that an alternative would have avoided.

Two specific failures motivate this ADR:

1. **Index-blind selection.** `Neo4jGraphStore.get_schema`
   (`seocho/store/graph.py:543`) returns labels / relationship types /
   property keys but does not call `SHOW INDEXES`, so the generator
   cannot prefer plans that hit an indexed property over plans that
   force a label scan. This is purely a missing-input problem.
2. **Single-plan emission.** `make_text2cypher_tool`
   (`seocho/tools.py:245`) returns the first template that matches the
   classified intent. Under-specified questions (typical of the
   `QueryEnrichmentRouter`'s fan-out input) have multiple plausible
   plans; emitting any one of them without ranking is the wrong default
   when cost differences are an order of magnitude.

Pattern selection is also tangled: intent-to-template logic lives inline
in `CypherBuilder.build` (`seocho/query/cypher_builder.py:53–286`) and
in the hardcoded `INTENT_CATALOG` tuple
(`seocho/query/intent.py:112–161`), so adding a new pattern requires
editing both. A pluggable catalog is the structural fix that lets the
cost ranker compare patterns on equal terms.

Pairs with ADR-0090 (tools surface), ADR-0091 (Cypher backend caller),
ADR-0092 (live property semantics the ranker reads). Subtask of
`seocho-j965`.

## Decision

Extend the Cypher backend with three composable layers and one
regression harness. None of this touches the public agent tool
signatures from ADR-0090; the change is internal to `make_text2cypher_tool`
and `CypherBuilder`.

### 1. Index-stats collector

New method `Neo4jGraphStore.get_index_stats(database, workspace_id)`
returning an `IndexStats` payload:

```
IndexStats = {
  indexes: [{name, kind, label/type, properties[], state, size?, selectivity?}],
  label_counts: {label: estimated_row_count},
  rel_counts: {rel_type: estimated_row_count},
}
```

Cypher source: `SHOW INDEXES`, plus per-label `MATCH (n:<label>) RETURN
count(n)` capped via sampling for large labels. Cached with the same
TTL/composite-key shape as `get_schema()` (60s default).

New tool factory `schema_with_stats` in `seocho/tools.py` that returns
the union of `get_schema()` and `get_index_stats()` so the Tier 2 prompt
can ground generation in both the schema and the index population. The
existing `schema_introspect` tool stays for callers that only need the
schema.

### 2. Externalized pattern catalog

Lift the inline intent-to-template logic from
`seocho/query/cypher_builder.py:53` and the hardcoded `INTENT_CATALOG`
into a registry of `PatternSpec` rows:

```
PatternSpec(
  pattern_id: str,
  intent_id: str,
  required_labels: list[str],
  required_relations: list[str],
  schema_preconditions: list[Precondition],   # e.g. "label X must have an index on prop Y"
  cost_hints: dict,                            # e.g. {"prefers_indexed": [...]}
  template_factory: Callable[..., CypherPlan],
)
```

The catalog is a module-level registry under
`seocho/query/pattern_catalog.py` with `register_pattern()` decorators.
`CypherBuilder` becomes a thin dispatcher that picks candidate patterns
from the catalog by intent + ontology constraints, hands them to the
ranker, and returns the ranked plans. `INTENT_CATALOG` stays as the
intent vocabulary; patterns reference intents by id.

ADR-0090's `cypher_template_lookup` tool contract is unchanged at the
boundary — the catalog is an internal refactor of how patterns are
discovered.

### 3. Plan enumeration + cost ranking

Inside `make_text2cypher_tool`, add a `candidate_plans: int = K` arm. The
flow becomes:

```
intent = classify(question)
candidates = pattern_catalog.match(intent, schema, constraint_slice)[:K]
plans = [c.template_factory(...) for c in candidates]
scored = [(plan, cost(plan, IndexStats)) for plan in plans]
return scored, sorted by ascending cost
```

`cost(plan, IndexStats)` is initially a deterministic linear model:

```
cost = α · plan_depth
     + β · estimated_row_count
     + γ · index_miss_penalty   # penalty per match that doesn't hit an index
     + δ · cartesian_risk
```

Coefficients live in `RoutingPolicy.thresholds` (default values land in
this ADR's implementation; per-workspace tuning is deferred). The ranker
emits **only the top-1 plan** to the validator and executor — multi-plan
execution is explicitly deferred to a follow-up. The full ranked list
plus per-plan cost breakdown lands in the JSONL/Opik trace so ranking is
auditable per CLAUDE.md §9.

Default `K = RoutingPolicy.thresholds["plan_candidates"]` (default `4`).
When intent classification confidence ≥ `intent_high`, `K = 1` (skip
enumeration to preserve the ADR-0091 short-circuit's latency benefit).

### 4. Evaluation harness

Extend `NLCypherExampleStore` (`seocho/store/vector.py`, used by
`tests/seocho/test_tiered_nl2cypher.py:45`) so each (NL, Cypher) write
also captures: `plan_cost_estimate`, `k_rank_position` (which rank the
emitted plan held), `execution_row_count`, `total_latency_ms`,
`enumeration_latency_ms`. Add a fixture suite of 20+ NL→Cypher pairs
under `tests/seocho/fixtures/gopts/` covering multi-candidate cases
(under-specified entity, ambiguous relationship, label-vs-name lookup).
This is the GOPTS regression anchor.

## Consequences

Positive:

- Cypher backend no longer emits an arbitrary plan when multiple are
  valid; ranked by a deterministic, auditable cost model
- index-aware plan selection eliminates a class of full-scan queries
  that today's index-blind generator can't avoid
- pattern catalog separation lets new domain patterns ship without
  touching `CypherBuilder` internals; aligns with the
  `feedback_explicit_interfaces` preference (no magic)
- evaluation harness gives the cost model a numeric regression target
  per `docs/PHILOSOPHY.md` (ontology-governed semantics with
  measurable promotion gates)
- the short-circuit (K=1 when intent confidence is high) keeps p50
  latency on common paths roughly equivalent to today's single-plan
  path

Tradeoffs:

- enumeration + ranking adds CPU cost per question (K template
  factories, K cost computations); mitigated by K=1 short-circuit and
  by the existing 60s schema cache extended to index stats
- the deterministic linear cost model is intentionally crude; it will
  systematically misrank in cases where index selectivity differs
  sharply from row counts (small but very selective index). The eval
  harness is the only honest way to detect this; per-workspace
  coefficient tuning is a deferred follow-up
- `SHOW INDEXES` and per-label counts add load to DozerDB at cache
  refresh; sampling for large labels caps the cost
- pattern catalog refactor is multi-file and must invoke the
  `refactor-safety` skill; risk concentrated in `cypher_builder.py`
- multi-plan execution (run all K, fuse results, beat single-plan
  recall) is **deferred**; this ADR only ranks and emits top-1

Open questions (deferred):

- whether `index_miss_penalty` should be per-property-type
  (string-property miss costs less than numeric range miss) or a flat
  constant — start with flat, measure on the eval suite
- whether the cost coefficients should be learned from the
  `NLCypherExampleStore` execution log over time (auto-tuning) or
  remain manual — start manual
- multi-plan execution (top-K execute + fuse) as a follow-up ADR if
  top-1 ranking proves insufficient

## Implementation Notes

- touch points:
  - `seocho/store/graph.py:543` — add `get_index_stats()` method;
    reuse the existing schema cache primitive
  - `seocho/tools.py:245` — extend `make_text2cypher_tool` with
    enumeration + ranking arm; add `schema_with_stats` tool factory
  - new module: `seocho/query/pattern_catalog.py` — `PatternSpec`,
    `register_pattern`, `match`
  - new module: `seocho/query/cost_model.py` — `cost()` function,
    coefficient defaults
  - `seocho/query/cypher_builder.py:53` — refactor inline patterns
    into catalog-registered entries; thin dispatcher remains
  - `seocho/query/intent.py:112` — `INTENT_CATALOG` stays as intent
    vocabulary; no longer carries template selection
  - `seocho/routing/__init__.py` — add `plan_candidates` and
    `intent_high` (if absent) to `RoutingPolicy.thresholds` defaults
  - `seocho/store/vector.py` — extend `NLCypherExampleStore` write
    schema with the four new fields
  - `tests/seocho/test_tiered_nl2cypher.py` — new fixture suite under
    `tests/seocho/fixtures/gopts/`
- safety skills to invoke during implementation: `refactor-safety`
  (catalog extraction is multi-file), `workspace-id-audit`
  (`get_index_stats` must scope to workspace_id, and the cost model
  must read workspace-scoped IndexStats — no cross-workspace leakage),
  `cypher-safety` (`SHOW INDEXES` and per-label `count(n)` Cypher must
  validate identifiers; cost model must not interpolate user input
  into Cypher), `owlready-boundary` (cost model reads compiled
  ontology context only; no request-time owlready2 calls)
- aligns with CLAUDE.md §6.1 (workspace propagation), §8 (DozerDB
  safety on `SHOW INDEXES`), §9 (per-plan cost in trace), §15 (no
  direct `Runner.run`; stays inside the QueryAgent tool surface), §18
  (cost model is deterministic; cache `IndexStats` for cache-friendly
  middleware ordering)
- depends on: ADR-0090 (tools surface), ADR-0091 (Cypher backend
  caller), ADR-0092 (live property semantics)
- enables: future ADR for multi-plan execution + result fusion
- parent tracking: `seocho-j965` (GraphAgenticLoop); this ADR is a
  Cypher-backend internal that the loop transparently benefits from.
- reference: GOPTS paper, VLDB 2024 LSGDA proceedings paper 04
  ("Graph-aware Plan Optimization for Text-to-Cypher").
