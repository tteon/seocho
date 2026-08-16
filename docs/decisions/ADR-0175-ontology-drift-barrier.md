# ADR-0175: ontology-drift read barrier — detect→enforce (seocho-ia4.1)

Date: 2026-08-16 · Status: accepted · seocho-ia4.1

## Context

A 3-lens design review (ontologist / MDM / OS-systems; see wiki/ontology-
lifecycle-os-design.md) found SEOCHO detects ontology/graph drift but never acts:
two verified bugs made "ontology-as-guardrail" silently enforce a stale contract.

1. **`GraphProjector.project()` never stamped the ontology version.** It wrote
   `workspace_id`/`graph_id`/`snapshot_id` but not `_ontology_context_hash` /
   `_ontology_version` / `_ontology_id` — the exact properties
   `build_ontology_context_summary_query` reads. So drift assessment on
   projector-written data saw empty hashes and was **blind**.
2. **The enforcement path was dead code.** `assess_ontology_context_mismatch` →
   `enforce_drift_policy(warn|raise|block)` are fully implemented but had **zero
   call sites** on the query path; `local_engine` only logged a warning.

"Strict without a freshness/drift check is a correctness bug, not safety" (hadry):
answering against data validated by a retired contract is a stale read of the type
table itself.

## Decision

Wire the already-built barrier (no new mechanism):
- `GraphProjector.project(..., ontology_context=)` stamps
  `ontology_context_graph_properties(context)` on every node and relationship;
  `local_engine.project_canonical_graph` resolves and passes the active context.
- `local_engine` and the `execute_cypher` tool now run
  `enforce_drift_policy(assess_..., policy=…)` instead of warn-only. Policy is
  `warn` (default, back-compat), `raise` (`OntologyDriftError`), or `block`
  (annotates `blocked=True` so the caller refuses to answer against stale data).
  Set via `SEOCHO_ONTOLOGY_DRIFT_POLICY` / `drift_policy`.

## Result — ablation, barrier OFF (today) vs ON (fixed)

`scripts/agentos/ablation_drift_barrier.py`: real `GraphProjector`, real
`query_ontology_context_mismatch` + `enforce_drift_policy`, deterministic
in-memory store; data written under ontology v1, queried under v1 or a breaking
v2. Worst / best / mixed scenarios.

| | OFF (today) | ON (fixed) |
|---|---|---|
| detection on real drift | **0%** | **100%** |
| false-positive on fresh data (null control) | 0% | **0%** |

- WORST (breaking bump, 100% stale): OFF serves it blind (0 caught); ON detects +
  blocks all of it.
- BEST / null (no bump, fresh data): both arms 0 false-positive — the barrier
  fires only on real drift, no fresh-data tax.
- MIXED (half v1/half v2 under v2): OFF blind, ON detects + blocks.

The fix flips drift detection from 0%→100% while staying silent on fresh data —
mostly wiring of existing code.

## Consequences

- First shipped step of the ontology-lifecycle OS (seocho-ia4). Turns detect-and-
  warn into an enforced read barrier; Trust/Safety + Long-Horizon paper tracks.
- 0 regressions (full suite: pre-existing failures unchanged); +4 unit tests.
- Caveat: `build_ontology_context_summary_query` scopes to `:Document` nodes; the
  ablation's fake store aggregates by stamp presence (the drift logic under test).
  Extending the summary scope beyond `:Document` and the freshness-*bound* barrier
  (bounded-staleness, refusal-ROC) are seocho-ia4.6.
- Follow-ups: version-pin/RCU (ia4.3), compatibility classifier (ia4.2), freshness
  controller + refusal-ROC experiment (ia4.6).
