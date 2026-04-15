# PropertyGraphLens Strategy

Date: 2026-04-15
Status: Active design plan

## Purpose

SEOCHO should preserve Neo4j/DozerDB's schemaless property graph advantage while
making the graph legible to agents.

The goal is not to force every node and edge into a rigid ontology. The goal is
to keep the raw property graph flexible, then mark a small set of important
nodes, relationships, paths, and provenance fields as agent-visible semantic
overlay.

Position:

```text
SEOCHO = schemaless property graph
       + ontology-guided semantic overlay
       + agent-visible evidence lens
```

## Core Idea

Property graphs are useful because nodes and relationships can carry arbitrary
properties. SEOCHO should use that flexibility instead of flattening it away.

The graph can remain heterogeneous:

- source-specific labels and properties are allowed
- partial or evolving data shapes are allowed
- not every node must map to an ontology class
- not every relationship must become a governed semantic relation

SEOCHO adds a lightweight overlay only where agent behavior benefits from it:

- anchor nodes that agents should consider first
- evidence sources that explain where claims came from
- evidence paths that should be preferred for grounded answers
- confidence, importance, provenance, and extraction metadata
- ontology context metadata for auditability

## Annotation Contract

First version annotations should be compact and optional.

Node properties:

- `_agent_visible: bool`
- `_seocho_role: "anchor" | "evidence_source" | "metric" | "claim" | "event" | "context"`
- `_importance: float`
- `_confidence: float`
- `_source_id: str`
- `_workspace_id: str`
- `_ontology_id: str`
- `_ontology_profile: str`
- `_ontology_context_hash: str`

Relationship properties:

- `_agent_visible: bool`
- `_seocho_edge_role: "evidence_path" | "supports" | "contradicts" | "temporal" | "causal" | "context"`
- `_importance: float`
- `_confidence: float`
- `_source_id: str`
- `_workspace_id: str`
- `_ontology_id: str`
- `_ontology_profile: str`
- `_ontology_context_hash: str`

These properties are hints, not schema enforcement. Missing annotations should
not make the graph invalid. They only affect what agents inspect first.

## What Agents Should See

Agents should not receive raw graph dumps. They should receive small,
deterministic views.

### 1. Schema Card

Compact database summary:

- labels
- relationship types
- indexed properties
- known `_seocho_role` values
- known `_seocho_edge_role` values
- ontology/profile/context metadata coverage

### 2. Property Profile

Small per-label or per-relationship profile:

- property names
- sample values
- null/missing ratio
- distinct count estimate when cheap
- provenance fields present or absent

### 3. Neighborhood Probe

Bounded 1-hop or 2-hop view around an anchor entity:

- anchor node
- visible outgoing/incoming edges
- neighboring visible nodes
- confidence and source metadata
- context mismatch warning if present

### 4. Evidence Path Bundle

Answer-facing evidence:

- selected nodes
- selected relationships
- selected paths
- slot fills
- missing slots
- provenance
- confidence

## OntologyRunContext Integration

`OntologyRunContext` should carry `property_graph_profile` and
`lens_policy` as optional fields.

Suggested fields:

- `property_graph_profile`: compact schema/property/overlay summary for the
  target database scope.
- `lens_policy`: read-only lens settings such as max hops, max nodes, role
  filters, and whether invisible graph elements can be used as fallback.
- `visible_roles`: allowed node roles for the current question.
- `visible_edge_roles`: allowed relationship roles for the current question.
- `evidence_state`: selected paths and missing-slot state after retrieval.

The context remains lightweight. It should not materialize large subgraphs.

## Query Strategy

Default query path:

1. Use ontology and graph scope to select database.
2. Read compact schema card.
3. Prefer `_agent_visible = true` anchors when resolving entities.
4. Prefer `_seocho_edge_role = "evidence_path"` for answer support.
5. Fall back to unannotated graph traversal only when visible evidence is
   insufficient.
6. Return evidence path bundle and missing-slot status.

Reasoning or repair path:

1. Inspect property profiles for likely missing labels/properties.
2. Probe visible neighborhoods around candidate anchors.
3. Retry with ontology-required relations and overlay edge roles.
4. Abstain or mark missing slots when no grounded path exists.

Debate path:

1. Each graph agent receives its own graph lens summary.
2. The supervisor compares answers with graph/profile provenance.
3. Grounded visible evidence beats broad but unmarked relevance.
4. Partial evidence remains visible instead of being hidden by fluent synthesis.

## Indexing Strategy

Indexing should not over-normalize every graph element.

Instead, mark special graph elements when the extractor or rule layer has enough
signal:

- high-confidence canonical entity -> `_seocho_role = "anchor"`
- document/chunk/source node -> `_seocho_role = "evidence_source"`
- extracted claim/event/metric -> domain role when confident
- relation used for answer support -> `_seocho_edge_role = "evidence_path"`
- relation expressing support/contradiction/time/causality -> matching edge role

When confidence is weak, write the raw property graph data without overlay
roles. The graph remains useful, but agent-first retrieval will not over-trust
it.

## Safety Rules

- Lens tools must be read-only by default.
- Dynamic labels and relationship types must be validated before Cypher
  interpolation.
- Lens queries must respect `workspace_id` and `allowed_databases`.
- Lens queries must have hard limits for hops, nodes, paths, and properties.
- The overlay must not hide policy violations. Authz/database scope remains a
  blocking check.
- Overlay absence is not an error. It is a retrieval signal.

## Non-Goals

- Do not convert SEOCHO into a rigid schema enforcement layer.
- Do not require every property graph element to carry ontology metadata.
- Do not run expensive graph analytics in the hot path.
- Do not expose full graph dumps to agents.
- Do not introduce graph embedding or Rust acceleration before measuring the
  Python/DozerDB baseline.

## User Interface Target

The SDK should let users keep their graph flexible while still getting
agent-grounded answers:

```python
result = (
    client.plan("Why did ACME's risk increase?")
    .on_graph("finance_kg")
    .with_repair_budget(2)
    .run()
)

print(result.response)
print(result.ontology_context_mismatch.get("warning", ""))
print(result.evidence.selected_triples if hasattr(result, "evidence") else [])
```

Longer term, a direct lens API can expose bounded graph introspection:

```python
lens = client.graph_lens("finance_kg")
print(lens.schema_card())
print(lens.neighborhood("ACME", max_hops=1))
```

The user experience should stay graph-native: the user can bring messy property
graph data, and SEOCHO marks the few graph elements that agents should trust
first.

## Implementation Plan

### Stage 1. Annotation Constants And Helpers

Add canonical constants/helpers for node and relationship overlay properties.
Keep them pure Python and dependency-free.

### Stage 2. Indexing Write Path

Attach overlay hints only for high-confidence anchors, evidence sources, and
evidence relationships. Do not annotate everything.

### Stage 3. Read-Only Lens Tools

Add bounded read-only helpers:

- `schema_card(database, workspace_id)`
- `property_profile(label_or_relationship, database, workspace_id)`
- `neighborhood(anchor, max_hops, database, workspace_id)`

### Stage 4. Evidence Bundle Integration

Prefer visible anchors and evidence paths when building selected triples and
slot fills.

### Stage 5. Debate Integration

Expose per-agent lens summaries in debate results so the supervisor can compare
evidence quality with graph/profile provenance.
