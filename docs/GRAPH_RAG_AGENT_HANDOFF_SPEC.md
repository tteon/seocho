# SEOCHO Graph-RAG Agent Handoff Spec

Date: 2026-04-09
Status: Active design brief

This note turns the finance tutorial failure analysis into a SEOCHO-specific
handoff contract for graph-grounded answering work.

Use it when redesigning or extending:

- `extraction/semantic_query_flow.py`
- `extraction/public_memory_api.py`
- `extraction/memory_service.py`
- answer grounding contracts in the memory-first SDK and runtime APIs

## Why This Brief Exists

SEOCHO already has the right building blocks:

- runtime raw ingest
- graph-aware memory APIs
- fulltext-first semantic resolution
- vocabulary and ontology hint layers
- debate and semantic answer paths

The remaining risk is familiar: graph retrieval can look good in traces while
the final answer still behaves like broad text retrieval with graph-flavored
hints.

The design target is therefore not "more graph metadata."

The design target is:

`question intent -> required slots -> selected subgraph -> evidence bundle -> grounded answer`

## Core Design Position

Treat the graph as the answer substrate, not as a weak reranking hint.

A graph-grounded SEOCHO answer is successful only when the runtime can:

1. infer the question intent
2. identify the required relation paths and answer slots for that intent
3. retrieve the specific subgraph that fills those slots
4. serialize the result into an inspectable evidence bundle
5. answer from that bundle while naming what is still missing

If the runtime only finds a broadly relevant neighborhood and then falls back to
nearby text, it is not yet solving the right problem.

## SEOCHO Requirements

### 1. Intent-First Retrieval

Every semantic answer path should map the question to a lightweight
`intent_id` with:

- `required_relations`
- `required_entity_types`
- `focus_slots`

The initial catalog can stay small and deterministic. Prefer:

- rule-based intent hints
- ontology/vocabulary-aware keyword triggers
- narrow prompt-assisted intent mapping only when rules are insufficient

Do not build a large router taxonomy before the minimal causal path works.

### 2. Evidence Bundle Contract

The retrieval layer should emit a compact internal bundle instead of only raw
candidate nodes or edge IDs.

Recommended fields:

- `intent_id`
- `candidate_entities`
- `selected_triples`
- `slot_fills`
- `missing_slots`
- `provenance`
- `confidence`
- `database` or `graph_id`

This bundle should be diffable, traceable, and suitable for API/debug payloads.

### 3. Missing Information Must Stay Visible

The answerer must not silently fill missing slots from nearby but ungrounded
text.

If required slots are absent:

- answer only from grounded fields
- name the missing slots
- avoid hallucinated slot completion

Conservative incompleteness is better than fluent fabrication.

### 4. Quality Signals Must Change Evidence Selection

Keep quality-aware logic only when it changes one of:

- edge inclusion
- edge ranking
- slot confidence
- abstention behavior

If a quality flag does not alter the chosen bundle, it is reporting metadata,
not answer-layer logic.

### 5. Fair Evaluation Keeps The Answerer Fixed

When comparing:

- question-only
- reference-only
- graph-grounded

keep the following fixed:

- answer model
- answer instruction
- output style
- context budget

Only the evidence source should change.

### 6. Failure States Must Be Inspectable

Runtime artifacts should make it obvious whether the miss came from:

- wrong intent
- wrong entity resolution
- missing required relations
- missing entity types
- partial slot fill
- low-confidence support
- abstained answer

This is required for honest evaluation and triage.

## Target Runtime Mapping

### Semantic Layer

Primary owner: `extraction/semantic_query_flow.py`

Add or expose:

- lightweight `IntentSpec`
- question-to-intent mapping
- slot-aware evidence selection
- explicit `missing_slots`

### Memory Service And Public API

Primary owners:

- `extraction/memory_service.py`
- `extraction/public_memory_api.py`

Expose enough grounding detail to inspect:

- which graph or database answered
- which memories or triples supported the answer
- which slots remained unresolved

### SDK And Typed Payloads

Primary owners:

- `seocho/types.py`
- `seocho/client.py`

Prefer typed evidence-bundle fields over opaque nested dicts where the public
contract needs to surface answer grounding.

## Anti-Patterns To Avoid

Do not drift into these patterns:

1. "Some relevant graph edges exist, so coverage is good."
2. "Graph evidence nudged sentence selection, so this counts as graph QA."
3. "Quality-aware logic exists in code, therefore it matters."
4. "A larger multi-agent architecture will solve slot-missing failures."
5. "Ontology-specific extraction is worse because a coarse proxy metric says so."

## Staged Implementation Plan

### Stage 1. Intent And Slot Contract

- add a small `intent_id` catalog for common memory and graph queries
- define `required_relations`, `required_entity_types`, and `focus_slots`
- keep the first implementation deterministic and auditable

### Stage 2. Evidence Bundle Selection

- rank candidate evidence by slot completion, not only local relevance
- emit `selected_triples`, `slot_fills`, `missing_slots`, and provenance
- make quality metadata affect ranking or abstention explicitly

### Stage 3. Grounded Answer Synthesis

- synthesize answers from the evidence bundle first
- keep text snippets as secondary support, not hidden primary evidence
- surface missing slots in the response contract

### Stage 4. Evaluation And Tracing

- keep answer-layer comparisons fair by fixing the answerer
- add trace fields that explain intent selection, slot fill, and abstention
- prefer small manual gold or answer-slot reviews over coarse proxy-only claims

## Acceptance Criteria

Treat a Graph-RAG change as acceptable only when it can show:

1. how the runtime infers `intent_id`
2. which slots and relations are required for that intent
3. which evidence filled those slots
4. which slots remained missing
5. how the final answer stayed grounded in that bundle
