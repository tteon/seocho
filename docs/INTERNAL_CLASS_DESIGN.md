# Internal Class Design

This page answers a different question than `docs/MODULE_OWNERSHIP_MAP.md`.

That page says *where code should live*. This page says *which internal classes
we should use to keep the modular monolith coherent while `seocho/client.py`
and runtime shells are still being decomposed*.

## Design Goal

SEOCHO is currently best modeled as a modular monolith with explicit internal
orchestration seams.

That means:

- keep the public SDK surface small: `add()`, `ask()`, runtime bundle helpers
- move orchestration helpers behind the facade
- prefer thin internal objects over broad service sprawl

## Recommended Internal Classes

| Class | File | Responsibility |
|---|---|---|
| `DomainEvent` | `seocho/events.py` | Small event envelope for trace/artifact/metrics hooks |
| `IngestionFacade` | `seocho/index/ingestion_facade.py` | Wrap indexing pipeline calls and publish lifecycle events |
| `QueryProxy` | `seocho/query/query_proxy.py` | Validate and instrument graph queries before they hit `GraphStore` |
| `AgentFactory` | `seocho/query/agent_factory.py` | Registry-backed construction of semantic/debate/query agents |
| `AgentStateMachine` | `runtime/agent_state.py` | Explicit runtime state transitions: ready, degraded, blocked |

## Import Graph

The intended import direction is:

```text
seocho/client.py
  -> seocho/local_engine.py
  -> seocho/index/ingestion_facade.py
  -> seocho/query/*
  -> seocho/events.py

runtime/*
  -> runtime/agent_state.py
  -> seocho/query/*
  -> seocho/index/*
  -> seocho/events.py

extraction/*
  -> runtime/* or seocho/* only as shim / legacy compatibility surface
```

What we want to avoid:

```text
seocho/query/* -> runtime/*
seocho/index/* -> extraction/*
seocho/client.py -> new business logic in extraction/*
```

## Current Wiring Plan

### 1. Local SDK path

`Seocho.add()` should stay the public entrypoint. Internally, the path becomes:

```text
Seocho.add()
  -> seocho/local_engine._LocalEngine.add()
  -> IngestionFacade.ingest()
  -> IndexingPipeline.index()
```

### 2. Query/runtime path

Today, canonical semantic orchestration already lives under `seocho/query/*`.
The next extraction step is not "new features" but "better object seams":

```text
runtime/server_runtime.py
  -> AgentFactory
  -> SemanticAgentFlow
  -> QueryProxy
  -> AgentStateMachine
```

Current wiring status:

- `runtime/server_runtime.py` now creates the shared semantic flow through the
  canonical `seocho.query.AgentFactory`
- `runtime/memory_service.py` and runtime Cypher tool execution now use
  `QueryProxy` for read/query instrumentation instead of bypassing the seam
- `runtime/agent_readiness.py` now normalizes debate readiness through
  `AgentStateMachine`
- the debate specialist-agent factory still remains on the legacy
  `extraction/agent_factory.py` path and should converge in a later slice

### 3. Event spine

The event layer should stay thin. It is not a distributed messaging system.

Good uses:

- trace lifecycle markers
- usage/cost instrumentation
- artifact publication hooks
- degraded/blocked state transitions

Bad uses:

- domain-wide async workflows
- cross-process guarantees
- background job orchestration

## Why This Fits SEOCHO

This design matches the current product shape:

- ontology-first orchestration, not generic agent hosting
- local first-run path, not a mandatory distributed deployment
- staged extraction-to-runtime migration, not a rewrite

In practical terms:

- `Factory` handles object construction
- `Facade` hides indexing complexity
- `Proxy` holds validation/instrumentation policy
- `State` makes readiness/degraded behavior explicit
- `Event` gives observability hooks without coupling core logic

## Relationship To Existing Files

- `seocho/client.py` remains the public facade for now
- `seocho/local_engine.py` owns local-mode orchestration behind that facade
- `seocho/client_remote.py` owns transport/request dispatch setup behind that facade
- `seocho/client_bundle.py` owns runtime-bundle import/export glue behind that facade
- `seocho/index/pipeline.py` remains the canonical indexing engine
- `seocho/query/semantic_flow.py` remains the canonical semantic orchestrator
- `runtime/server_runtime.py` remains the runtime composition root

These new classes are internal seams for decomposition, not a replacement for
the current public API.
