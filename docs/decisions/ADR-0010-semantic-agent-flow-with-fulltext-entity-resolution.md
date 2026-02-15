# ADR-0010: Semantic Agent Flow With Fulltext Entity Resolution

- Status: Accepted
- Date: 2026-02-15
- Deciders: SEOCHO team

## Context

Graph QA quality is currently limited by query-time entity resolution:

- users ask in natural language with imperfect entity strings
- graph stores canonical entities with varying names/labels
- direct Cypher generation can target wrong entities without disambiguation

The product direction also requires an explicit 4-agent flow after semantic
and extraction layers.

## Decision

Introduce a semantic graph QA route with the following sequence:

1. Semantic layer:
   - extract entities from question
   - resolve candidates with DozerDB/Neo4j fulltext search
   - fallback to contains-based lookup
   - rank and dedup candidates using lexical + label-hint scoring
2. RouterAgent:
   - choose `lpg`, `rdf`, or `hybrid` path from question intent
3. Specialist agents:
   - LPGAgent for property-graph neighborhood retrieval
   - RDFAgent for RDF/ontology-oriented retrieval
4. AnswerGenerationAgent:
   - synthesize routed results into final response

Implementation surface:

- endpoint: `POST /run_agent_semantic`
- module: `extraction/semantic_query_flow.py`

## Rationale

- fulltext-first lookup improves recall for query-time entity matching
- explicit semantic dedup/disambiguation reduces wrong-node selection
- dedicated flow keeps legacy `/run_agent` and `/run_debate` stable

## Consequences

Positive:

- better control over entity resolution behavior
- clear separation of semantic layer and agent layer responsibilities
- easier future extension for ontology-informed reranking

Trade-offs:

- adds one more runtime path to test and observe
- deterministic rule-based routing may underperform for ambiguous intent

## Guardrails

- use read-only Cypher patterns in semantic route
- keep heavy ontology reasoning (e.g., Owlready2) out of request hot path
- include semantic resolution metadata in trace output
