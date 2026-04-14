# ADR-0060: Semantic Query Phase C Route-Agent Canonicalization

**Date:** 2026-04-15
**Status:** Accepted
**Authors:** hadry

## Context

`ADR-0056` Phase C moves the semantic-query route agents out of
`extraction/semantic_query_flow.py` and into canonical `seocho/query/*`
ownership.

After Phase B, the remaining query-runtime classes still living in extraction
were:

- `SemanticEntityResolver`
- `QueryRouterAgent`
- `LPGAgent`
- `RDFAgent`
- `AnswerGenerationAgent`

These classes contain the query-runtime behavior that determines how question
entities are resolved, how semantic support is evaluated, how constrained LPG
queries are built and repaired, how RDF fallback is queried, and how the final
answer text is framed.

Leaving them in `extraction/` would keep the SDK and server runtime on
different route-agent owners even though the underlying query contracts were
already canonicalized in Phases A and B.

## Decision

Land Phase C of `ADR-0056` by moving the route-agent layer into canonical
`seocho/query/semantic_agents.py`.

`extraction/semantic_query_flow.py` keeps its public import surface, but the
agent names now rebind to canonical SEOCHO implementations before
`SemanticAgentFlow` instantiates them.

## Scope

New canonical owner:

- `seocho/query/semantic_agents.py`

Canonicalized classes:

- `SemanticEntityResolver`
- `QueryRouterAgent`
- `LPGAgent`
- `RDFAgent`
- `AnswerGenerationAgent`

Compatibility behavior:

- `extraction/semantic_query_flow.py` still exports the same names
- `SemanticAgentFlow` now instantiates canonical Phase C classes
- graph target registry remains injected from extraction transport/runtime shell
- `SemanticAgentFlow` itself still remains in extraction for Phase D

## Consequences

Positive:

- semantic query route-agent behavior now has a canonical owner under
  `seocho/query/*`
- SDK and runtime shells can converge on the same entity resolution and route
  agent code
- Phase D can focus on moving `SemanticAgentFlow` itself instead of re-litigating
  route-agent ownership

Tradeoffs:

- helper support for ontology hints, vocabulary aliasing, and semantic profile
  packages is copied into the canonical route-agent module during the migration
  window
- `extraction/semantic_query_flow.py` still carries legacy class definitions
  until the final flow move lands

## Validation

- focused route-agent tests in `seocho/tests/`
- semantic query flow regression in `extraction/tests/`
- API endpoint regression for semantic query compatibility
- parity harness regression

## References

- ADR-0056: canonicalize semantic query flow to SDK
- ADR-0058: semantic query Phase A pure-logic canonicalization
- ADR-0059: semantic query Phase B DB-aware support canonicalization
