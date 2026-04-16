# Graph Memory API Draft

Date: 2026-03-12
Status: Draft

This document defines the target public interface for SEOCHO when the product direction emphasizes a graph-memory-style interaction model on the outside.

Important scope boundary:

- SEOCHO is not trying to replicate any specific peer product
- SEOCHO borrows only the generic graph-memory interaction model
- graph-backed retrieval, provenance, rules, and governance remain differentiators

## 1. Goals

The public API should feel memory-first, not orchestration-first.

That means:

- store memories
- search memories
- ask questions from memories
- inspect retrieval evidence

The user should not need to understand:

- debate orchestration
- router/LPG/RDF mode selection
- rule export APIs
- semantic artifact lifecycle details

Those remain internal or expert surfaces.

One exception is graph selection. Public callers may pass `graph_ids` as routing hints, but they should not need to know internal agent names or orchestration topology.

## 2. Public vs Internal Surface

### Public Surface

These are stable application-facing APIs.

- `POST /api/memories`
- `POST /api/memories/batch`
- `GET /api/memories/{memory_id}`
- `POST /api/memories/search`
- `DELETE /api/memories/{memory_id}`
- `POST /api/chat`
- `GET /graphs`
- `GET /api/traces/{trace_id}`

### Internal / Expert Surface

These remain available for platform use, admin workflows, or advanced debugging.

- `/platform/ingest/raw`
- `/run_agent_semantic`
- `/run_debate`
- `/rules/*`
- `/semantic/artifacts/*`
- `/indexes/fulltext/ensure`

## 3. Resource Model

### Memory

A memory is the user-facing resource.

Suggested fields:

- `memory_id`
- `workspace_id`
- `user_id`
- `agent_id`
- `session_id`
- `content`
- `metadata`
- `created_at`
- `updated_at`
- `status`

Internal graph-derived fields may exist, but should not dominate the public payload.

Optional evidence fields:

- `entities`
- `relations`
- `source_refs`
- `trace_id`
- `evidence_bundle`

### Trace

Represents how retrieval and answer generation happened.

Suggested fields:

- `trace_id`
- `workspace_id`
- `memory_ids`
- `retrieval_strategy`
- `route`
- `explanations`
- `created_at`

### Evidence Bundle

For graph-grounded search or chat flows, SEOCHO may return an inspectable
evidence bundle with fields such as:

- `intent_id`
- `required_relations`
- `required_entity_types`
- `focus_slots`
- `candidate_entities`
- `selected_triples`
- `slot_fills`
- `missing_slots`
- `provenance`
- `confidence`

The initial implementation may return partial bundles while slot-aware evidence
selection expands beyond entity-match scaffolding.

## 4. Scoping Model

`workspace_id` remains mandatory.

Optional secondary scopes:

- `user_id`
- `agent_id`
- `session_id`

Suggested rule:

- every public memory operation requires `workspace_id`
- caller may additionally narrow the memory namespace with `user_id`, `agent_id`, and `session_id`

## 5. Endpoint Draft

### 5.1 Create Memory

`POST /api/memories`

Request:

```json
{
  "workspace_id": "default",
  "user_id": "user_123",
  "agent_id": "assistant_main",
  "session_id": "sess_001",
  "content": "Alice manages the Seoul retail account.",
  "metadata": {
    "source": "manual_note",
    "tags": ["account", "org"]
  }
}
```

Advanced request extensions:

- `approved_artifact_id`: apply one approved semantic artifact by ID
- `approved_artifacts`: apply explicit approved ontology / SHACL / vocabulary payloads
- `metadata.semantic_prompt_context`: per-request ontology/vocabulary/linking guidance for advanced workflows

Recommended precedence:

1. graph target metadata
2. approved semantic artifacts
3. `metadata.semantic_prompt_context` overrides
4. runtime draft candidates

Response:

```json
{
  "memory": {
    "memory_id": "mem_01H...",
    "workspace_id": "default",
    "user_id": "user_123",
    "agent_id": "assistant_main",
    "session_id": "sess_001",
    "content": "Alice manages the Seoul retail account.",
    "metadata": {
      "source": "manual_note",
      "tags": ["account", "org"]
    },
    "status": "stored",
    "created_at": "2026-03-12T10:00:00Z"
  },
  "ingest_summary": {
    "entities_detected": 2,
    "relations_detected": 1,
    "trace_id": "tr_01H..."
  }
}
```

### 5.2 Batch Create

`POST /api/memories/batch`

Use for importing many memory items with a consistent scope.

### 5.3 Get Memory

`GET /api/memories/{memory_id}?workspace_id=default`

Returns one memory plus optional evidence summary.

### 5.4 Search Memory

`POST /api/memories/search`

Request:

```json
{
  "workspace_id": "default",
  "user_id": "user_123",
  "query": "Who manages the Seoul account?",
  "limit": 5
}
```

Response:

```json
{
  "results": [
    {
      "memory_id": "mem_01H...",
      "content": "Alice manages the Seoul retail account.",
      "score": 0.93,
      "reasons": ["entity_match", "graph_neighbor_match"],
      "source_refs": [],
      "evidence_bundle": {
        "intent_id": "responsibility_lookup",
        "focus_slots": ["owner_or_operator", "target_entity", "supporting_fact"],
        "slot_fills": {
          "target_entity": "Seoul retail account"
        },
        "missing_slots": ["owner_or_operator"],
        "confidence": 0.93
      }
    }
  ],
  "trace_id": "tr_01H..."
}
```

### 5.5 Delete or Archive Memory

`DELETE /api/memories/{memory_id}`

Open product question:

- hard delete
- soft delete
- archive

For MVP, a soft-delete or archive model is safer.

### 5.6 Chat From Memory

`POST /api/chat`

Request:

```json
{
  "workspace_id": "default",
  "user_id": "user_123",
  "session_id": "sess_001",
  "message": "What do we know about the Seoul account?",
  "graph_ids": ["kgnormal"]
}
```

Response:

```json
{
  "assistant_message": "Alice manages the Seoul retail account.",
  "memory_hits": [
    {
      "memory_id": "mem_01H...",
      "score": 0.93
    }
  ],
  "trace_id": "tr_01H..."
}
```

### 5.7 List Graph Targets

`GET /graphs`

Response:

```json
{
  "graphs": [
    {
      "graph_id": "kgnormal",
      "database": "kgnormal",
      "uri": "bolt://neo4j:7687",
      "ontology_id": "baseline",
      "vocabulary_profile": "vocabulary.v2",
      "description": "Baseline enterprise graph for general entity extraction and retrieval.",
      "workspace_scope": "default"
    }
  ]
}
```

This is the discovery surface for graph-aware retrieval and debate. It exposes graph IDs and ontology/vocabulary metadata, not internal agent names.

### 5.8 Graph Debate Surface

`POST /run_debate`

This remains an expert/runtime API, but the contract is now graph-scoped:

```json
{
  "workspace_id": "default",
  "user_id": "user_123",
  "query": "Compare what the baseline and finance graphs know about Alice.",
  "graph_ids": ["kgnormal", "kgfibo"]
}
```

Each `graph_id` maps to a graph target descriptor with `uri`, `database`, `ontology_id`, and `vocabulary_profile`. That lets one OpenAI Agents SDK agent bind to one graph target, even when different graph IDs point at different Neo4j instances.

## 6. Response Consistency Rules

All public endpoints should follow these patterns:

### Success

- top-level resource key or `results`
- stable IDs
- timestamps in ISO 8601
- `trace_id` when retrieval or inference occurred

### Errors

Use one error envelope:

```json
{
  "error": {
    "code": "validation_error",
    "message": "workspace_id is required",
    "request_id": "req_123"
  }
}
```

Do not mix:

- `detail`
- raw strings
- nested arbitrary error formats

## 7. Mapping To Current Implementation

The target public API can initially be a facade over current internals.

Suggested mapping:

- `POST /api/memories` -> wraps `/platform/ingest/raw`
- `POST /api/memories/search` -> wraps semantic retrieval flow
- `POST /api/chat` -> wraps `/platform/chat/send`
- `GET /api/traces/{trace_id}` -> wraps Opik or stored trace metadata

This allows interface cleanup before deeper backend rewrites.

## 8. Architectural Implications

To support this interface cleanly, the backend should:

1. separate public facade routes from internal expert routes
2. define a stable memory service layer
3. normalize response and error envelopes
4. keep graph-specific orchestration behind the facade

## 9. Open Questions

1. Is memory creation synchronous for MVP, or should ingest become async for larger documents?
2. Are `user_id`, `agent_id`, and `session_id` all optional, or should one of them be required in addition to `workspace_id`?
3. What retrieval evidence should be visible by default versus expert mode?
4. Should `/api/memories/search` support structured filters on metadata in MVP?
5. Should chat always return `memory_hits`, even when the answer is weak or empty?
