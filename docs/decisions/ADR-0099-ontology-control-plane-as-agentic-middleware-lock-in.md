# ADR-0099: Ontology Control Plane as Agentic Middleware Lock-In

Date: 2026-06-03

Status: Accepted

## Context

SEOCHO should not depend on owning a graph database engine or a foundation model
provider. Those layers can improve independently and remain replaceable.

The defensible SEOCHO layer is the middleware between agents and graph stores:
choosing the right ontology profile, compiling it into cheap runtime hints,
injecting it into query/indexing/debate/reasoning flows, and measuring whether a
candidate profile actually improves quality, latency, and cost.

Headroom is a useful analogy: its moat is not a model, but a middleware layer
that routes, compresses, caches, and makes context reversible before it reaches
the model. SEOCHO's equivalent layer is ontology selection, compilation,
evaluation, and promotion.

## Decision

Add a canonical `seocho.ontology_control_plane` module with:

- `OntologySignal` for indexing-side and query-side discoveries
- `OntologyProfile` for user-reviewable versioned ontology profiles
- `CompiledOntologyProfile` for hot-path injection into routing, text-to-Cypher,
  multi-agent aggregation, debate, reasoning, and answer synthesis
- `OntologyProfileRegistry` for the minimal profile registry contract
- `OntologyControlPlane` for deterministic profile selection and
  baseline-vs-candidate evaluation

This slice intentionally stays in Python and in-memory. Persistent storage,
runtime endpoints, UI, and CI matrices can wrap the same contract later.

## Consequences

- Query-side and indexing-side ontology signals now have a shared typed shape.
- Users can review expected effect before approving a profile:
  quality delta, latency delta, cost delta, and suggested controls.
- Runtime hot paths consume compiled aliases, required slots, route hints, and
  answer shapes rather than heavy ontology reasoning.
- SEOCHO's lock-in becomes the closed-loop ontology middleware, not hidden model
  prompts or a proprietary database backend.

## Follow-Up

- Persist profiles and signals in the runtime artifact store.
- Expose profile diff, approve, rollback, and regression rerun endpoints.
- Add MARA/OpenAI-compatible E2E matrix gates for baseline vs candidate profile.
- Make `EvidenceBundle` consumers use `CompiledOntologyProfile.required_slots`
  and `answer_shapes` directly.
