# Runtime Shell Refactor Plan

Date: 2026-04-26
Status: Draft

This document is the repo-current architecture target and slice-1 refactor
plan for the runtime shell.

It replaces the older assumption that `extraction/agent_server.py` is the
server implementation. In the current repository, `runtime/agent_server.py` is
the canonical runtime shell and `extraction/agent_server.py` is a compatibility
alias.

## 1. Architecture Thesis

SEOCHO should converge to a modular monolith with:

- a tiny stable public facade for SDK users
- canonical engine behavior in `seocho/*`
- deployment-shell behavior in `runtime/*`
- `extraction/*` limited to compatibility wrappers and offline helpers

That means:

- complexity should be hidden at the API boundary
- complexity should be named explicitly inside the codebase
- each concern should have one canonical owner

This plan is aligned with:

- `docs/ARCHITECTURE.md`
- `docs/INTERNAL_CLASS_DESIGN.md`
- `docs/MODULE_OWNERSHIP_MAP.md`
- `docs/RUNTIME_PACKAGE_MIGRATION.md`

## 1.5 Architecture Principles

The runtime-shell refactor should preserve the product idea, not only move
files around.

Principles:

- ontology is a middleware contract, not a rigid total schema
- the property graph stays flexible; the ontology supplies the semantic overlay
- complexity should be hidden from users, but named explicitly for maintainers
- provenance, policy, and traceability are first-class runtime concerns
- `seocho/*` owns canonical behavior; `runtime/*` composes it; `extraction/*`
  should not regain ownership through convenience
- hot-path behavior must stay lightweight: no heavy ontology reasoning in
  request-time routes

In practical terms:

- users should keep a small interface such as `Seocho.add()` and `Seocho.ask()`
- agents should receive compact ontology/run-context metadata instead of raw,
  magical prompt shaping
- runtime routes should expose mismatch and evidence metadata rather than
  silently hiding ontology drift or weak grounding

## 2. Desired Package Ownership

### `seocho/*` — canonical engine

Owns:

- public SDK facade
- local orchestration
- ontology contracts and artifacts
- indexing and linking
- query orchestration
- graph/vector/LLM adapters
- vendor-neutral tracing

Must not depend on:

- `runtime/*`
- `extraction/*` business logic

### `runtime/*` — deployment shell

Owns:

- FastAPI application wiring
- HTTP route modules
- request/response translation
- runtime policy checks
- readiness and degraded-state handling
- runtime composition root and shared service initialization

Should compose `seocho/*`, not duplicate its logic.

### `extraction/*` — compatibility and offline helpers

Allowed:

- flat import aliases
- migration shims
- batch-only or offline utilities where a canonical owner is not yet moved

Disallowed:

- new canonical runtime features
- new canonical query or ontology logic

## 3. Public Surface Rules

The public product surface should stay intentionally small.

### SDK surface

Primary SDK entrypoints:

- `Seocho.local()`
- `Seocho.add()`
- `Seocho.ask()`
- runtime bundle helpers

The `Seocho` facade should remain stable while orchestration moves behind:

- `seocho/local_engine.py`
- `seocho/index/ingestion_facade.py`
- `seocho/query/*`
- `seocho/ontology_*`

### Runtime HTTP surface

Public-facing or user-facing routes should be stable in contract even if their
implementation moves:

- `/platform/chat/send`
- `/platform/ingest/raw`
- `/run_agent`
- `/run_agent_semantic`
- `/run_debate`
- `/health/runtime`
- `/health/batch`
- `/rules/*`
- `/semantic/artifacts/*`
- public memory router under `runtime/public_memory_api.py`

### Extension surface

The supported plugin surface remains narrow:

- graph store
- vector store
- LLM backend
- embedding backend

Everything else is internal and may be refactored freely.

## 4. Target Runtime Shell Shape

Current problem: `runtime/agent_server.py` combines too many concerns:

- FastAPI app creation
- middleware and exception handling
- startup boot logic
- local tool definitions
- inline agent definitions
- request and response models
- route implementations
- router inclusion

The target shape is:

```text
runtime/
  agent_server.py           # temporary composition root only
  app_factory.py            # target home for app creation
  middleware.py
  policy.py
  server_runtime.py         # shared service factories
  agent_state.py
  agent_readiness.py
  memory_service.py
  public_memory_api.py

  models/
    common.py
    platform.py
    query.py
    debate.py
    health.py
    semantic.py

  routes/
    platform.py
    query.py
    debate.py
    health.py
    admin.py
    rules.py
    semantic_artifacts.py

  tools/
    graph.py
    runtime_metadata.py
```

Target responsibilities:

- `agent_server.py`
  - create app
  - register middleware
  - register exception handlers
  - include routers
- `server_runtime.py`
  - own shared service factories and lazy initialization
- `models/*`
  - request/response schemas only
- `routes/*`
  - thin route handlers, policy checks, response mapping
- `tools/*`
  - temporary home for route-internal tool helpers until they converge into
    canonical `seocho/*` seams

## 5. Internal Seams To Preserve

The runtime shell should not invent new orchestration seams when we already
have the correct internal shapes.

Keep and strengthen:

- `IngestionFacade`
- `QueryProxy`
- `AgentFactory`
- `AgentStateMachine`
- `DomainEvent`

Runtime import direction should be:

```text
runtime/routes/*
  -> runtime/server_runtime.py
  -> runtime/policy.py
  -> seocho/query/*
  -> seocho/index/*
  -> seocho/events.py
```

Avoid:

```text
runtime/routes/* -> extraction/* business logic
seocho/* -> runtime/*
seocho/query/* -> FastAPI request models
```

## 6. Slice 1: Concrete Refactor Plan For `runtime/agent_server.py`

This slice is intentionally narrow. It is a readability and maintainability
slice, not a behavior redesign.

### 6.1 Scope

In scope:

- split `runtime/agent_server.py` by concern
- keep route paths unchanged
- keep response contracts unchanged
- move models and route handlers into dedicated modules

Out of scope:

- changing runtime semantics
- redesigning ontology/routing policy
- replacing `server_runtime.py`
- converging the legacy debate agent factory

### 6.2 Phase 1A — extract runtime API models

Move request/response classes out of `runtime/agent_server.py` into
`runtime/models/*`.

Suggested first split:

- `runtime/models/query.py`
  - `QueryRequest`
  - `EntityOverride`
  - `SemanticQueryRequest`
  - `AgentResponse`
  - `SemanticAgentResponse`
  - `SemanticRunRecordResponse`
  - `SemanticRunRecordListResponse`
  - `DebateResponse`
- `runtime/models/platform.py`
  - `PlatformChatRequest`
  - `PlatformTurn`
  - `PlatformChatResponse`
  - `PlatformSessionResponse`
  - `RawIngestRecord`
  - `PlatformRawIngestRequest`
  - `RawIngestError`
  - `RawIngestWarning`
  - `PlatformRawIngestResponse`
- `runtime/models/health.py`
  - `HealthComponent`
  - `HealthResponse`
- `runtime/models/common.py`
  - `ErrorDetail`
  - `ErrorResponse`

Acceptance:

- `runtime/agent_server.py` imports these models instead of defining them inline
- no route behavior changes

### 6.3 Phase 1B — extract route modules

Create `runtime/routes/*` modules and move endpoint functions there.

Suggested split:

- `runtime/routes/platform.py`
  - `/platform/chat/send`
  - `/platform/chat/session`
  - `/platform/ingest/raw`
- `runtime/routes/query.py`
  - `/run_agent`
  - `/run_agent_semantic`
  - `/semantic/runs`
  - `/semantic/runs/{id}`
  - `/indexes/fulltext/ensure`
  - `/databases`
  - `/graphs`
  - `/agents`
- `runtime/routes/debate.py`
  - `/run_debate`
- `runtime/routes/health.py`
  - `/health/runtime`
  - `/health/batch`
- `runtime/routes/rules.py`
  - `/rules/*`
- `runtime/routes/semantic_artifacts.py`
  - `/semantic/artifacts/*`

Acceptance:

- `agent_server.py` becomes router inclusion plus startup wiring
- route paths and models stay stable

### 6.4 Phase 1C — isolate shell-only helpers

Move shell-local helper logic into dedicated modules without changing behavior.

Candidates:

- `get_databases_impl()`
- `get_graphs_impl()`
- `get_schema_impl()`
- any inline tool wrappers that exist only for runtime shell composition

Preferred destination:

- `runtime/tools/runtime_metadata.py`
- `runtime/tools/graph.py`

Do not move canonical query logic out of `seocho/query/*` into these helpers.

### 6.5 Phase 1D — reduce `agent_server.py` to composition root

The end state for slice 1 is that `runtime/agent_server.py` contains only:

- imports
- `FastAPI(...)`
- middleware registration
- exception handlers
- startup/shutdown hooks
- router inclusion

Rough target:

```text
runtime/agent_server.py
  app = FastAPI(...)
  app.add_middleware(...)
  @app.exception_handler(...)
  @app.on_event("startup")
  app.include_router(...)
```

That file should stop being the main home for runtime behavior.

## 7. Guardrails During Refactor

The refactor must preserve these contracts:

- `workspace_id` remains propagated
- runtime policy checks remain explicit
- no Owlready2 or heavy governance in request hot path
- JSONL/Opik tracing semantics remain intact
- user activation critical path still passes:
  - raw ingest
  - semantic/debate query
  - UI trace inspection
  - `make e2e-smoke`

## 8. Definition Of Done

This plan is successful when:

1. `runtime/agent_server.py` is readable as a shell/composition file
2. route handlers are organized by surface, not by historical growth order
3. request/response models are separated from orchestration code
4. canonical engine logic stays in `seocho/*`
5. `extraction/*` does not gain new canonical runtime behavior

## 9. Follow-On Slices

After slice 1 lands, the next architecture slices should be:

1. converge any remaining live query/orchestration seams from legacy
   `extraction/*` into canonical `seocho/query/*`
2. formalize `runtime/app_factory.py` if route/module growth makes
   `agent_server.py` still too large
3. add contract tests that assert route-module extraction did not change:
   - response shape
   - policy behavior
   - trace metadata presence
   - critical-path UX flows
