# ADR-0013: Custom Interactive Chat Platform Replaces Streamlit

- Status: Accepted
- Date: 2026-02-15
- Deciders: SEOCHO team

## Context

The previous UI (`evaluation/app.py`) was Streamlit-based and suitable for demos.
The product now needs a dedicated interactive chat platform with tighter control over:

- frontend interaction model,
- backend session contracts,
- semantic disambiguation loop integration.

## Decision

1. Replace Streamlit runtime path with custom frontend backend:
   - evaluation service now runs `evaluation/server.py` (FastAPI static host + proxy)
   - UI shipped as static assets in `evaluation/static/*`
2. Add specialized platform agents in backend:
   - `BackendSpecialistAgent` for mode execution dispatch
   - `FrontendSpecialistAgent` for UI payload shaping
3. Add custom platform APIs:
   - `POST /platform/chat/send`
   - `GET /platform/chat/session/{session_id}`
   - `DELETE /platform/chat/session/{session_id}`

## Rationale

- Enables product-grade interaction without Streamlit framework constraints.
- Keeps existing graph/agent runtimes reusable behind stable platform contracts.
- Supports semantic candidate override loop as first-class UX capability.

## Consequences

Positive:

- clearer separation of frontend and backend responsibilities
- more controllable UX and API evolution
- platform session primitives ready for future persistence/auth integration

Trade-offs:

- larger code surface than Streamlit PoC
- additional API compatibility and UI integration tests required

## Guardrails

- runtime authorization remains enforced in `agent_server`
- platform backend remains an adapter over existing safe execution paths
- no heavy ontology reasoning added to hot path
