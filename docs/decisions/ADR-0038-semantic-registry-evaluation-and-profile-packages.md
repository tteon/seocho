# ADR-0038: Semantic Registry, Evaluation, And Profile Package Contract

Date: 2026-04-11
Status: Accepted

## Context

After ADR-0037, SEOCHO emitted richer semantic execution metadata, but three
gaps remained:

- semantic run history was still best-effort file output instead of a queryable
  registry surface
- developers had no built-in way to compare `question_only`,
  `reference_only`, `semantic_direct`, and `semantic_repair` on manual gold
  cases
- deterministic ontology/profile hints and cross-graph disagreement signals
  were not first-class in the semantic runtime

This kept debugging possible, but still too ad hoc for sustained developer use.

## Decision

SEOCHO will standardize the next semantic developer surface around five pieces:

1. SQLite-backed semantic run registry
   - semantic runs are recorded outside DozerDB into a queryable SQLite store
   - runtime API exposes `GET /semantic/runs` and `GET /semantic/runs/{run_id}`
   - SDK exposes `semantic_runs()` and `semantic_run()`
2. Manual-gold evaluation harness
   - the Python SDK exposes `SemanticEvaluationHarness`
   - supported baselines are `question_only_baseline`,
     `reference_only_baseline`, `semantic_direct`, and `semantic_repair`
   - metrics stay retrieval-grounded: `intent_match_rate`, `support_rate`,
     `required_answer_slot_coverage_manual`, and
     `preferred_evidence_hit_rate`
3. Deterministic semantic profile packages
   - known ontology/intent combinations may prioritize deterministic relation
     families without bypassing runtime validation
4. Disagreement-aware advanced recommendation
   - cross-graph disagreement becomes an explicit signal inside
     `strategy_decision`
   - advanced debate remains opt-in, but the recommendation becomes more
     evidence-based
5. Public typed SDK access
   - run registry records become typed SDK values rather than raw dicts

## Consequences

Positive:

- semantic runs are inspectable without tailing internal files
- baseline comparisons become repeatable and developer-friendly
- ontology-aligned query families become more deterministic without giving up
  bounded repair
- advanced mode recommendations rely on detectable graph disagreement instead
  of only broad failure heuristics

Tradeoffs:

- the runtime now maintains a SQLite sidecar in addition to graph storage
- evaluation metrics are intentionally narrow and do not replace answer-quality
  review
- deterministic profile packages need ongoing curation as ontology coverage
  expands

## Implementation Notes

- registry store: `extraction/semantic_run_store.py`
- semantic runtime integration: `extraction/semantic_query_flow.py`
- runtime API exposure: `extraction/agent_server.py`
- public SDK surface: `seocho/client.py`, `seocho/models.py`, `seocho/evaluation.py`
