# ADR-0058: Semantic Query Phase A Pure-Logic Canonicalization

**Date:** 2026-04-13
**Status:** Accepted
**Authors:** hadry

## Context

`ADR-0056` established `seocho/query/*` as the canonical home for semantic
query orchestration, but the first concrete slice still needed to be small
enough to land without entangling DB-aware code or HTTP transport.

The lowest-risk slice is the pure-logic layer inside
`extraction/semantic_query_flow.py`:

- intent contract and catalog
- evidence-bundle shaping
- support assessment helpers
- execution-strategy selection
- Cypher plan validation
- insufficiency classification

These types and helpers do not need direct graph access. Leaving them in
`extraction/` would keep the local SDK and server runtime on different query
contracts even after `ADR-0056`.

## Decision

Land Phase A of `ADR-0056` by moving the pure-logic semantic query primitives
into canonical `seocho/query/*` modules and rewiring the extraction path to
instantiate those canonical classes.

## Scope

New canonical modules:

- `seocho/query/intent.py`
- `seocho/query/strategy_chooser.py`
- `seocho/query/cypher_validator.py`
- `seocho/query/insufficiency.py`

Canonical contracts added to `seocho/query/contracts.py`:

- `IntentSpec`
- `CypherPlan`
- `InsufficiencyAssessment`

Extraction compatibility behavior:

- `extraction/semantic_query_flow.py` keeps its public import surface
- runtime agent instances bind to canonical SEOCHO query classes through
  aliases, not local duplicate implementations
- DB-aware support classes, route agents, and `SemanticAgentFlow` itself remain
  in `extraction/` for later phases

## Consequences

Positive:

- local SDK and server runtime now share the same semantic query intent,
  validation, insufficiency, and strategy contracts
- Phase B can focus on DB-aware helpers without re-litigating pure logic
- tests can assert canonical module ownership directly

Tradeoffs:

- `extraction/semantic_query_flow.py` still contains dead local definitions
  during the migration window
- full semantic-query parity still depends on later `ADR-0056` phases

## Validation

- focused semantic query unit tests in `seocho/tests/`
- focused semantic flow regression tests in `extraction/tests/`
- no parity-harness gate in this slice because the current harness environment
  in the landing clone is not yet reliable enough to act as a clean blocker

## References

- ADR-0048: canonical query engine first slice
- ADR-0056: canonicalize semantic query flow to SDK
