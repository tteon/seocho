# SEOCHO MVP PRD

Date: 2026-03-12
Status: Draft

## 1. Product Statement

SEOCHO is a self-hosted memory and knowledge platform for teams that want a mem0-open-source-like graph memory interface on top of a graph-backed runtime.

Important scope boundary:

- the target is not the full mem0 product
- the target is the graph memory interaction model

The product experience should feel simple and memory-first from the outside, while keeping graph extraction, rule inference, ontology governance, and tracing inside the platform.

## 2. Problem

Current graph and agent systems are often difficult to adopt because:

- ingestion is separated from retrieval and chat
- graph internals leak into the public interface
- memory retrieval is brittle when users do not know schema or exact entity names
- observability and governance are bolted on instead of being part of the core workflow

Users want a simpler interface:

- add memory
- search memory
- ask questions over memory
- inspect why a memory was used
- control scope by user, session, agent, and workspace

## 3. Target Outcome

Build an interface that feels as direct as a graph memory product, while retaining SEOCHO's stronger graph and governance capabilities behind the scenes.

The public experience should emphasize:

- memory CRUD and search semantics
- simple namespaces and filters
- predictable API contracts
- fast onboarding
- self-hosted local-first operation

The internal architecture may still use:

- OpenAI Agents SDK
- DozerDB
- vendor-neutral tracing (`none|console|jsonl|opik`)
- Opik as an optional team observability backend
- SHACL-like rule inference
- approved semantic artifacts and vocabulary resolution

## 4. Primary Users

### 4.1 Builder / Application Developer

Needs:

- a simple API to store and retrieve memory
- deterministic request and response contracts
- enough observability to debug retrieval and answer quality

### 4.2 Platform / Data Engineer

Needs:

- reliable ingestion and graph loading
- workspace-aware policy enforcement
- operational visibility, health checks, and repeatable quality gates

### 4.3 Reviewer / Knowledge Curator

Needs:

- visibility into extracted facts and rules
- artifact approval workflow
- provenance and readiness checks before promotion

## 5. Product Principles

1. Memory-first interface, graph-backed implementation.
2. Public API should expose resources, not internal orchestration jargon.
3. Provenance, traceability, and governance are product features, not debug extras.
4. `workspace_id` must remain first-class in all runtime-facing contracts.
5. Heavy ontology reasoning stays offline.
6. Semantic and debate orchestration are implementation details unless the user explicitly asks for them.

## 6. MVP Scope

### In Scope

- raw content ingestion through a simple public path
- persistent memory records backed by graph extraction and storage
- semantic search and chat over stored memory
- workspace-aware memory isolation
- minimal memory management operations:
  - create
  - get
  - search
  - delete or archive
- trace visibility for memory retrieval and answer generation
- operational health and e2e smoke validation

### Out Of Scope

- multi-tenant SaaS control plane
- collaborative editing workflows
- enterprise auth integrations
- advanced visual ontology authoring
- fully general-purpose graph administration UI
- heavy ontology reasoning in hot runtime paths

## 7. MVP User Flows

### Flow A: Add Memory

1. User submits text or structured content.
2. Platform stores the raw item.
3. Platform extracts entities, relationships, rules, and vocabulary hints.
4. Platform links the item into the graph store.
5. User receives a stable memory identifier and ingest result summary.

### Flow B: Search Memory

1. User submits a query with scope fields such as `workspace_id`, `user_id`, `agent_id`, or `session_id`.
2. Platform resolves vocabulary and entity aliases.
3. Platform returns ranked memory results with provenance and relevance reasons.

### Flow C: Ask From Memory

1. User submits a question.
2. Platform resolves relevant memories and graph context.
3. Platform answers in a memory-first format.
4. Platform exposes trace or retrieval evidence on demand.

### Flow D: Review Governance

1. Reviewer inspects semantic artifact draft, rule profile, and readiness status.
2. Reviewer approves or deprecates artifacts.
3. Runtime uses only the approved baseline when the selected policy requires it.

## 8. Interface Direction

The current repo is graph-centric in naming. The target product should become memory-centric in its public interface.

### Current Public Surface

- `/platform/ingest/raw`
- `/platform/chat/send`
- `/run_agent_semantic`
- `/run_debate`
- `/rules/*`
- `/semantic/artifacts/*`

### Target Public Surface Direction

The external interface should move toward resource-oriented APIs such as:

- `POST /api/memories`
- `GET /api/memories/{memory_id}`
- `POST /api/memories/search`
- `DELETE /api/memories/{memory_id}`
- `POST /api/chat`
- `GET /api/traces/{trace_id}`
- `GET /api/workspaces/{workspace_id}/memories`

Internal graph, debate, rule, and artifact endpoints may still exist, but should be treated as platform-internal or expert-mode surfaces.

## 9. MVP Acceptance Criteria

The MVP is acceptable when a new user can:

1. run the stack locally in under 10 minutes
2. add sample memory from the UI or API
3. search and retrieve that memory with sensible ranking
4. ask a question and receive a graph-backed answer
5. inspect why the answer was produced
6. observe consistent behavior across `workspace_id` boundaries
7. complete the critical path with an e2e smoke check

## 10. Non-Functional Requirements

### API and Contract

- public endpoints must be consistent in naming and response structure
- error payloads must be structured and predictable
- stable IDs must be returned for stored memory and traces

### Reliability

- runtime and batch health must be separated
- degraded behavior must be explicit
- core flows must have smoke coverage

### Performance

- semantic and debate modes must expose measurable latency and cost
- retrieval should degrade gracefully before timing out

### Governance

- readiness checks gate promotion
- artifact approval is explicit
- provenance is retained end-to-end

## 11. Success Metrics

### Activation

- first successful local run rate
- time to first successful ingest
- time to first successful search and chat answer

### Product Quality

- retrieval precision for top results
- answer usefulness on curated questions
- percentage of requests with usable provenance and trace metadata

### Operational Quality

- e2e smoke pass rate
- runtime error rate
- p95 latency by mode
- rule readiness pass rate for promoted artifacts

## 12. Risks

- current API surface leaks graph/runtime internals into the user experience
- route naming and payload shapes are not yet consistently memory-centric
- `agent_server.py` centralizes too many responsibilities
- ambiguity around `semantic/` and embedded website workspace increases contributor error risk

## 13. Open Product Questions

1. Should memory objects be first-class persisted resources independent of graph nodes, or are graph-backed records enough for MVP?
2. Which scopes are mandatory in the public interface: `workspace_id`, `user_id`, `agent_id`, `run_id`, `session_id`?
3. Should semantic and debate be selectable user-facing modes, or should they remain internal routing strategies?
4. What retrieval explanation format should be public by default?
5. What is the minimum delete policy for MVP: hard delete, soft delete, or archive only?

## 14. Release Gate

Do not call the MVP ready unless the following path works end-to-end:

1. ingest memory
2. ensure semantic index if required
3. search or ask from memory
4. inspect trace or retrieval evidence
5. pass smoke validation
