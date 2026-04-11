# ADR-0037: Semantic Support Validation And Strategy Metadata Contract

Date: 2026-04-11
Status: Accepted

## Context

SEOCHO already had intent-first semantic retrieval, bounded repair, and explicit
advanced debate mode. What it still lacked was a clear developer-facing answer
to four questions:

- does this graph likely support the current question?
- why did the runtime stay on semantic mode or recommend escalation?
- what grounded evidence was actually filled versus still missing?
- where can a semantic run be inspected after the request finishes?

Without those signals, `semantic(...)` and `plan(...).run()` worked, but they
were still too opaque for systematic debugging and application-level control.

## Decision

SEOCHO will standardize four runtime outputs for semantic graph execution:

1. `support_assessment`
   - intent-aware preflight and post-execution support summary
   - includes support status, reason, coverage, grounded slots, and missing slots
2. `strategy_decision`
   - explicit summary of initial mode, executed mode, self-reflection usage,
     and whether advanced debate is recommended
3. `evidence_bundle.v2`
   - compact grounded bundle with `grounded_slots`, `missing_slots`,
     `selected_triples`, provenance, and support linkage
4. `run_metadata`
   - lightweight JSONL registry entry recorded outside the graph store

These outputs must be available in both the runtime API and the public Python
SDK, with developer-friendly accessors on `SemanticRunResponse`.

## Consequences

Positive:

- developers can inspect semantic execution without reading raw trace steps
- debate stays explicitly advanced instead of becoming an invisible default
- retrieval quality is easier to tune around intent support rather than broad
  graph coverage
- runtime debugging gets a separate metadata plane without polluting DozerDB

Tradeoffs:

- semantic responses become more verbose
- the runtime now maintains one more best-effort sidecar artifact
- support scoring is heuristic and will need refinement as intent coverage grows

## Implementation Notes

- semantic runtime owner: `extraction/semantic_query_flow.py`
- API exposure: `extraction/agent_server.py`
- SDK typed accessors: `seocho/models.py`
- registry target defaults to `/tmp/seocho/semantic_run_registry.jsonl`
