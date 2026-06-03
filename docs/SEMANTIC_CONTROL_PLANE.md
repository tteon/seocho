# Semantic Control Plane

Date: 2026-04-26
Status: Active design plan

## Purpose

SEOCHO should let users edit ontology in a portable format such as JSON-LD and
have that change govern the whole agent stack.

Product promise:

- edit ontology, not hidden prompts
- keep semantics user-owned and portable
- make indexing, query, routing, tool use, and evaluation derive from one
  semantic source of truth

This is the right kind of stickiness for SEOCHO. The system becomes hard to
replace because it operationalizes user semantics everywhere, not because it
traps those semantics in opaque vendor state.

## Core Position

SEOCHO should converge on this shape:

```text
user-authored ontology + vocabulary + design specs
-> semantic compiler
-> compiled semantic package
-> indexing plane
-> query/agent plane
-> evaluation + tracing
```

One semantic control plane should govern two execution planes:

- indexing/data plane
- query/agent plane

The semantic control plane is not a separate microservice requirement. It is a
canonical contract and module boundary inside the modular monolith.

## Ontology Control Plane Slice

The first implementation slice is `seocho.ontology_control_plane`.

It makes the control plane concrete without changing the graph backend or model
provider:

- `OntologySignal`: indexing-side and query-side discoveries such as aliases,
  missing slots, drift, route failures, and answer regressions
- `OntologyProfile`: a user-reviewable, versioned profile containing ontology
  candidates, vocabulary candidates, SHACL candidates, route hints, answer
  shapes, and measured metrics
- `CompiledOntologyProfile`: the hot-path artifact consumed by routing,
  text-to-Cypher, debate, reasoning, and answer synthesis
- `OntologyControlPlane`: deterministic profile selection and baseline-vs-
  candidate evaluation for approve/rollback/rerun workflows

This is SEOCHO's lock-in layer: users own portable semantics, but SEOCHO owns
the operational loop that chooses, compiles, measures, and promotes the right
ontology profile for each workspace and query.

Runtime and SDK user controls now cover the first closed loop:

- `POST /semantic/ontology-signals`: record indexing/query discoveries
- `GET /semantic/ontology-signals`: inspect signal history
- `POST /semantic/ontology-profiles`: create or update a profile
- `GET /semantic/ontology-profiles`: list profile candidates by status
- `GET /semantic/ontology-profiles/{profile_id}/compiled`: inspect the hot-path
  artifact that agents will consume
- `POST /semantic/ontology-profiles/select`: see which profile SEOCHO would use
  for a query and why
- `POST /semantic/ontology-profiles/{profile_id}/evaluate`: compare a candidate
  against a baseline with expected quality/latency/cost deltas
- `POST /semantic/ontology-profiles/{profile_id}/promote`: approve the profile
  selected by the review workflow

## User Contract

The user workflow should stay simple:

1. edit `schema.jsonld` or another supported ontology input
2. optionally add indexing or agent design overlays
3. run local SDK or runtime ingestion/query
4. inspect traces and results keyed to the same semantic package identity

The important rule is that user-edited ontology changes must have real,
inspectable system effect:

- extraction labels and relationship hints change
- entity resolution aliases and type hints change
- intent slots and required relations change
- graph lens visibility and evidence preferences change
- tool middleware and evaluation policy can change

## Canonical Artifact: `SemanticPackage`

Indexing and query should not parse raw ontology independently in ad hoc ways.
They should consume one compiled package.

Suggested contract:

- `package_id`
- `package_version`
- `ontology_id`
- `ontology_profile`
- `vocabulary_profile`
- `ontology_context_hash`
- `glossary_hash`
- `entity_types`
- `relationship_types`
- `property_terms`
- `extraction_context`
- `entity_resolution_hints`
- `intent_catalog`
- `required_relations_by_intent`
- `required_entity_types_by_intent`
- `focus_slots_by_intent`
- `property_graph_lens_policy`
- `evidence_policy`
- `tool_policy_hints`
- `evaluation_profile`

This package should be:

- deterministic
- hashable
- diffable in git-friendly form
- cheap to cache
- usable by both local SDK and runtime paths

## Authoring Inputs

The semantic package can be compiled from:

- ontology files such as JSON-LD
- vocabulary or glossary overlays
- indexing design specs
- agent design specs
- approved runtime semantic artifacts

JSON-LD should remain the primary portable format, but SEOCHO can allow friendlier
authoring overlays as long as they compile to the same canonical package model.

## Layer Responsibilities

### Compiler Layer

Owns:

- authoring-input validation
- ontology/profile/glossary normalization
- package hashing and versioning
- compilation into one reviewable semantic package

Target module direction:

- `seocho/ontology.py` remains the public facade
- `seocho/ontology_context.py` continues to own shared hashes and compact
  runtime metadata
- add a target compiled-package owner such as `seocho/semantic_package.py`
- add a target compiler owner such as `seocho/ontology_compiler.py`
- add a target resolver/facade such as `seocho/semantic_control_plane.py`

These names are target architecture, not a big-bang requirement.

### Indexing Plane

The indexing plane should consume the semantic package for:

- extraction prompt context
- canonical labels, relationship terms, and aliases
- graph-model-aware materialization defaults
- overlay annotations such as `_agent_visible` and evidence roles
- ontology metadata written onto graph nodes and relationships
- validation and readiness defaults

Indexing should not guess its own ontology contract once a package exists.

### Query And Agent Plane

The query plane should consume the same semantic package for:

- entity resolution hints
- intent inference
- required slots and relations
- evidence-bundle construction
- abstention and missing-slot policy
- property-graph lens policy
- bounded repair behavior

This is the core alignment requirement:

```text
same semantic package
-> same ontology hashes
-> same intent and slot rules
-> same evidence policy
for both indexing and query
```

### Runtime And Tool Middleware

Runtime middleware should use the semantic package plus request scope for:

- allowed database scope
- tool budget hints
- ontology context mismatch reporting
- session drift detection across turns
- debate-agent context summaries

The runtime should not treat ontology as hidden prompt text. It should surface
the active semantic package identity and mismatch status in traces and response
metadata.

### Evaluation And Tracing

Every benchmark run, trace, and semantic run record should capture:

- `package_id`
- `package_version`
- `ontology_context_hash`
- `workspace_id`
- route/executed mode
- support status
- slot fill / missing-slot state
- tool-call count
- latency and cost

That is what makes ontology changes measurable instead of anecdotal.

## Bottleneck Scorecard

Each major module should be evaluated on contract correctness, quality
contribution, and latency/cost.

### Compiler

- compile latency
- cache hit rate
- package diff clarity
- invalid authoring detection rate

### Indexing

- ingest p50/p95 latency
- nodes/relationships created
- fallback extraction rate
- deduplication rate
- ontology metadata coverage on writes
- graph projection loss rate

### Query

- entity-resolution precision
- route precision
- support coverage
- slot-fill rate
- missing-slot rate
- abstention quality
- query p50/p95 latency

### Tool Use And Multi-Turn

- useful tool-call ratio
- unnecessary tool-call ratio
- tool-budget compliance
- clarification success rate
- turns-to-resolution
- context-drift detection rate

### Answering

- grounded answer rate
- unsupported answer rate
- missing-slot disclosure rate
- answer latency

## Release-Gate Rule

SEOCHO should compare behavior by semantic package, not only by dataset or model
name.

A benchmark summary is incomplete unless it can answer:

- which semantic package produced this result?
- did latency improve or worsen under the same package?
- did answer quality improve because of retrieval, routing, or ontology change?
- did a package change help indexing, query, or both?

## Non-Negotiables

- users own the ontology and semantic inputs
- indexing and query consume one compiled semantic package
- ontology mismatch is visible in responses and traces
- heavy ontology reasoning stays out of the hot path
- evaluation records semantic package identity on every important run

## Phased Delivery

### Stage 1. Contract

- publish the semantic control plane architecture
- define the semantic package contract
- record package identity in traces and semantic run metadata

### Stage 2. Compiler

- add a canonical ontology-to-package compiler seam
- make JSON-LD plus overlays resolve to one package model

### Stage 3. Index And Query Convergence

- make indexing consume package-derived extraction/materialization hints
- make query consume package-derived intent, slot, and evidence rules

### Stage 4. Evaluation Gates

- key benchmark and runtime summaries by package identity
- compare ontology revisions as first-class experiment variants
