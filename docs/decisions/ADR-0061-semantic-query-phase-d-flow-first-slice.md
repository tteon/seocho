# ADR-0061: Semantic Query Phase D Flow Canonicalization

**Date:** 2026-04-15
**Status:** Accepted
**Authors:** hadry

## Context

`ADR-0056` reserved Phase D for the final migration of `SemanticAgentFlow`
itself out of `extraction/semantic_query_flow.py` and into canonical
`seocho/query/*` ownership.

After Phases A, B, and C, the remaining extraction-owned semantic query logic
was the orchestration shell that:

- runs semantic entity resolution
- builds per-database constraint slices
- applies entity overrides
- coordinates LPG/RDF specialists
- records semantic run metadata
- assembles the semantic answer payload

At that point, keeping the flow class in extraction would leave the SDK and
runtime shells with canonical support classes but no canonical orchestration
owner.

## Decision

Land Phase D of `ADR-0056` by introducing canonical
`seocho/query/semantic_flow.py` and rebinding
`extraction/semantic_query_flow.py::SemanticAgentFlow` to the canonical class.

The extraction runtime shell continues to inject graph targets from
`graph_registry`, but the orchestration owner is now `seocho/query/*`.

## Scope

New canonical owner:

- `seocho/query/semantic_flow.py`

Canonicalized class:

- `SemanticAgentFlow`

Compatibility behavior:

- `extraction/semantic_query_flow.py` still exports `SemanticAgentFlow`
- the exported name now resolves to the canonical SEOCHO flow class
- `extraction/server_runtime.py` passes runtime graph targets into the
  canonical flow on construction

## Consequences

Positive:

- semantic query orchestration now has a canonical owner under `seocho/query/*`
- `extraction/semantic_query_flow.py` is reduced to a compatibility surface
  instead of being a second query engine
- future cleanup can delete dead legacy class definitions in extraction without
  changing ownership again

Tradeoffs:

- compatibility aliases remain in place during the migration window
- extraction still carries historical helper code until a later cleanup slice
  removes dead definitions

## Validation

- focused semantic flow tests in `seocho/tests/`
- semantic query flow regression in `extraction/tests/`
- API endpoint regression for semantic query compatibility
- parity harness regression

## References

- ADR-0056: canonicalize semantic query flow to SDK
- ADR-0058: semantic query Phase A pure-logic canonicalization
- ADR-0059: semantic query Phase B DB-aware support canonicalization
- ADR-0060: semantic query Phase C route-agent canonicalization
