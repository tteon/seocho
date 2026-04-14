# ADR-0059: Semantic Query Phase B DB-Aware Support Canonicalization

**Date:** 2026-04-13
**Status:** Accepted
**Authors:** hadry

## Context

`ADR-0056` defined Phase B as the migration of DB-aware semantic-query support
classes out of `extraction/semantic_query_flow.py` and into canonical
`seocho/query/*` modules.

After Phase A, the remaining high-value support classes were:

- `SemanticConstraintSliceBuilder`
- `RunMetadataRegistry`

These types are runtime-aware because they resolve graph-target metadata,
approved semantic artifacts, and semantic run metadata persistence. They are
still narrow support classes, not route agents or `SemanticAgentFlow` itself.

Leaving them in `extraction/` would keep the canonical query engine incomplete
and would force future agent-flow slices to keep depending on extraction-owned
query state.

## Decision

Land Phase B of `ADR-0056` by introducing canonical DB-aware support modules:

- `seocho/query/constraints.py`
- `seocho/query/run_registry.py`

`extraction/semantic_query_flow.py` keeps its compatibility surface, but
runtime instances now bind to the canonical support classes.

## Scope

New canonical modules:

- `seocho/query/constraints.py`
- `seocho/query/run_registry.py`

Compatibility behavior:

- `SemanticConstraintSliceBuilder` remains importable from
  `extraction/semantic_query_flow.py`
- `RunMetadataRegistry` remains importable from
  `extraction/semantic_query_flow.py`
- `LPGAgent` and `SemanticAgentFlow` instantiate canonical support classes
- route agents and `SemanticAgentFlow` itself still remain in `extraction/`
  for later `ADR-0056` phases

## Consequences

Positive:

- semantic query constraint slices now have a canonical owner under
  `seocho/query/*`
- semantic run metadata persistence now has a canonical owner under
  `seocho/query/*`
- later agent and flow slices can target behavior instead of support-state
  ownership

Tradeoffs:

- semantic artifact and semantic run store helpers are copied into canonical
  query support modules during the migration window
- `extraction/semantic_query_flow.py` still contains legacy class definitions
  until later phases remove them entirely

## Validation

- focused semantic query support tests in `seocho/tests/`
- focused semantic query flow regression tests in `extraction/tests/`
- API endpoint regression for semantic-query path compatibility
- parity harness regression

## References

- ADR-0056: canonicalize semantic query flow to SDK
- ADR-0058: semantic query Phase A pure-logic canonicalization
