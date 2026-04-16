# Agent Server Refactor Plan

Date: 2026-03-12
Status: Draft

This document describes how to split `extraction/agent_server.py` into a clearer architecture that supports a memory-first public interface while preserving current runtime behavior.

## 1. Why Refactor

`extraction/agent_server.py` currently combines too many concerns:

- FastAPI app creation
- middleware setup
- error handling
- request and response models
- import-time singleton creation
- route definitions
- runtime orchestration wiring
- admin and expert APIs

This makes it harder to:

- evolve public APIs cleanly
- test behavior in isolation
- reuse services across routes
- distinguish stable public contracts from internal runtime surfaces

## 2. Refactor Goals

1. introduce a memory-first public facade
2. separate public APIs from expert/internal APIs
3. remove unnecessary import-time side effects
4. make dependencies explicit and injectable
5. keep current behavior working during migration

## 3. Target Architecture

Suggested shape:

```text
extraction/
  api/
    app.py
    errors.py
    dependencies.py
    models/
      common.py
      memories.py
      chat.py
      health.py
      rules.py
      semantic_artifacts.py
    routes/
      public_memories.py
      public_chat.py
      health.py
      expert_runtime.py
      rules.py
      semantic_artifacts.py
  services/
    memory_service.py
    chat_service.py
    semantic_query_service.py
    ingest_service.py
  runtime/
    connectors.py
    factories.py
    state.py
```

This does not require rewriting all business logic at once. It gives a stable destination.

## 4. Public vs Expert Route Split

### Public

These routes should become the long-term stable interface:

- `/api/memories`
- `/api/memories/batch`
- `/api/memories/search`
- `/api/chat`
- `/api/traces/{trace_id}`
- `/health/runtime`
- `/health/batch`

### Expert / Internal

These routes may continue to exist but should be treated as implementation surfaces:

- `/platform/ingest/raw`
- `/platform/chat/send`
- `/run_agent`
- `/run_agent_semantic`
- `/run_debate`
- `/rules/*`
- `/semantic/artifacts/*`
- `/indexes/fulltext/ensure`

## 5. Module Responsibilities

### `api/app.py`

- create FastAPI application
- register middleware
- register routers
- wire exception handlers

### `api/dependencies.py`

- create dependency providers
- move runtime initialization behind callables
- avoid global import-time singleton construction where possible

### `api/models/*`

- request and response models only
- no business logic

### `api/routes/*`

- thin handlers
- policy checks
- request validation
- call into service layer

### `services/*`

- orchestration and application logic
- mapping between public resources and graph runtime

### `runtime/*`

- connectors
- low-level factories
- shared runtime state

## 6. Migration Phases

### Phase 0: Characterization

Before moving code:

- keep or expand existing endpoint tests
- identify current response shapes
- mark routes as public versus expert

### Phase 1: Model Extraction

- move request and response models out of `agent_server.py`
- keep imports wired back into the existing file

Low risk, high clarity gain.

### Phase 2: Dependency Extraction

- move connector and manager initialization into dependency providers
- reduce import-time state creation

This is the most important technical cleanup.

### Phase 3: Router Extraction

- split route handlers by surface
- keep the same route paths initially
- preserve tests to prevent accidental contract drift

### Phase 4: Public Memory Facade

- add `/api/memories*` and `/api/chat`
- implement as thin facade over existing services
- normalize response and error envelopes

### Phase 5: Internal Route Clarification

- document old endpoints as expert/internal
- decide deprecation policy
- migrate UI and clients gradually

## 7. API Consistency Improvements

If the goal is a generic graph-memory-style interface, these improvements matter most:

### Naming

- stop mixing `run_*`, `platform/*`, and resource-style names at the same public level
- use noun-based public APIs

### Response shape

- standardize success envelopes
- standardize error envelopes
- include `trace_id` consistently for inference-heavy operations

### IDs

- return stable `memory_id` for stored memory
- keep graph node IDs internal unless explicitly debugging

### Scoping

- keep `workspace_id` required
- define optional `user_id`, `agent_id`, and `session_id` uniformly across memory endpoints

### Modes

- hide route-selection detail from public callers by default
- expose semantic versus debate only as debug or expert metadata when needed

## 8. Risks During Refactor

- accidental response shape drift
- auth or policy regressions
- duplicated logic between old and new endpoints
- dependency initialization changes breaking startup behavior

Mitigation:

- characterization tests first
- move code in thin slices
- keep old endpoints until facade is stable

## 9. Recommended Immediate Work

### P0

1. extract models from `agent_server.py`
2. extract dependency wiring
3. add `public_memories` and `public_chat` routers as facades

### P1

4. normalize error envelopes
5. define stable memory resource IDs
6. move graph-specific fields behind expert mode

### P2

7. deprecate or hide legacy public-facing route names
8. move toward an app-factory structure

## 10. Definition Of Done

This refactor is successful when:

1. public memory-first routes exist
2. internal expert routes are clearly separated
3. `agent_server.py` stops being the single source of every API concern
4. dependency initialization becomes easier to test
5. current critical path still passes smoke validation
