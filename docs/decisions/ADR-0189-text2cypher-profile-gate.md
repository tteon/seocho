# ADR-0189: text2cypher metric-threshold profile gate (seocho-ia4)

Date: 2026-08-16 · Status: accepted (gate primitive) · seocho-ia4

## Context

text2cypher is the last optimization stage before the GDBMS; cost varies by orders of
magnitude. hadry wants an agent that detects an "optimization needed" signal, profiles,
and improves before querying. The AIsummit26 harness already has a detect→profile→improve
loop but gates ONLY on a 2s wall-clock probe and merely UNLOCKS a hint tool (reminder:
wiki/text2cypher-optimization-design.md).

## Decision

`query/profile_gate.py` generalizes that into a multi-signal, auto-driving gate:
- `PlanMetrics` (db_hits, estimated_rows, elapsed_ms, operators, rows_returned, used_index)
  + `ProfileThresholds` (the "optimization needed" signal).
- `evaluate_plan` DETECTS breaches (db_hits/estimated_rows/SLO/rows over budget; full-scan
  without index; cartesian product), PROFILES the plan, and emits an actionable
  `improve_directive` the agent feeds back into a repair turn — auto-driven, not just
  unlocked.
- `parse_explain_metrics` walks a DozerDB/neo4j EXPLAIN/PROFILE tree (duck-typed).
Deterministic and DB-free → testable without a live graph.

## Consequences

- The missing DETECT→IMPROVE trigger for the text2cypher loop; the rest (grounding
  ADR-0187, generate_validated_cypher, AIsummit26 harness, bolt-rs) is reuse.
- +7 tests. Follow-ups: wire into generate_validated_cypher's repair turn; the
  ontology-source ablation (introspected vs ontology-declared schema — the sharp "is the
  ontology useful to text2cypher" axis, not yet run on the agent half); the bolt-rs LLM
  episode loop.
