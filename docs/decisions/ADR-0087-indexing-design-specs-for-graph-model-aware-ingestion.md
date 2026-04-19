# ADR-0087: Indexing Design Specs For Graph-Model-Aware Ingestion

- Status: Accepted
- Date: 2026-04-19

## Context

SEOCHO already supports ontology-first indexing, but the user-facing contract
did not let teams declare:

- whether they want LPG, RDF, or hybrid ingestion
- what storage target the design expects
- which provenance or inference posture applies
- how anomaly-driven inquiry should be handled when SHACL or answer support
  fails

That made graph-model-specific ingestion behavior implicit and harder to review.

## Decision

Introduce YAML-backed `IndexingDesignSpec` as a local SDK construction contract.

The design spec:

- requires an explicit ontology binding
- declares `graph_model` (`lpg`, `rdf`, `hybrid`)
- declares `storage_target` (`ladybug`, `neo4j`, `dozerdb`)
- carries ingestion/materialization/provenance settings
- carries an optional `reasoning_cycle` block for anomaly-driven inquiry

`Seocho.from_indexing_design(...)` materializes the ontology graph model,
installs stable indexing defaults, and injects reviewable design metadata into
local `add()` / `add_batch()` calls.

For LPG designs, the SDK also installs a property-graph-oriented extraction
prompt by default so the LLM emits property-friendly payloads instead of
forcing everything through an RDF-like post-normalization path.

## Reasoning Cycle Contract

The `reasoning_cycle` block models inquiry as:

1. anomaly detection
2. abduction
3. deduction
4. induction

Guardrail:

- abductive output stays candidate-only by default
- it must not silently become canonical fact
- verification or analyst approval is required before promotion

## Consequences

Positive:

- graph-model-aware ingestion is reviewable in git
- LPG and RDF paths get explicit, user-visible design choices
- SHACL and support failures can be tied to a bounded inquiry contract
- ontology-first indexing gains a reusable public surface similar to agent
  design specs

Tradeoffs:

- the first slice only applies to local SDK construction
- `reasoning_cycle` is metadata/prompt guidance first, not a full hot-path
  orchestrator
- runtime-side ingestion and promotion workflows remain follow-up work
