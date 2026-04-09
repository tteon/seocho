# ADR-0031: Intent-First Graph-RAG Evidence Bundle Contract

Date: 2026-04-09
Status: Accepted

## Context

SEOCHO's semantic and memory-first runtime now has graph-aware ingest, entity
resolution, vocabulary hints, and graph-scoped routing, but that is not enough
to guarantee graph-grounded answers.

The main remaining risk is that runtime answers can still degrade into:

- broad neighborhood retrieval
- text-first synthesis with graph hints
- coverage claims that are too coarse to explain answer quality

The finance Graph-RAG review work from 2026-03-15 showed the same failure mode:
graph structure can improve without improving final answers unless the runtime is
explicitly optimized for question-supporting structured evidence.

## Decision

SEOCHO will adopt an intent-first internal contract for graph-grounded
answering:

1. map each semantic graph question to a lightweight `intent_id`
2. define intent requirements in terms of:
   - `required_relations`
   - `required_entity_types`
   - `focus_slots`
3. make retrieval emit a compact evidence bundle rather than only raw
   edge or node selections
4. require the answer layer to synthesize from that bundle first and surface
   missing slots explicitly
5. keep answer-layer evaluations fair by fixing the answerer and changing only
   the evidence source

## Consequences

Positive:

- graph-grounded answers become easier to inspect and debug
- slot-completion quality becomes visible in runtime traces and API payloads
- the semantic route can be evaluated on answerability instead of vague graph
  presence

Tradeoffs:

- the runtime contract becomes more explicit and therefore more verbose
- a small curated intent catalog is required before broader automation
- some existing retrieval logic may need to be re-ranked around slot coverage,
  not only local relevance

## Implementation Notes

- design brief lives in `docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md`
- primary runtime owners:
  - `extraction/semantic_query_flow.py`
  - `extraction/memory_service.py`
  - `extraction/public_memory_api.py`
- typed payload follow-up should prefer explicit grounding structures in
  `seocho/types.py`
