# ADR-0106: Semantic Control Plane User-Owned Ontology Contract

- Status: Accepted
- Date: 2026-04-26

## Context

SEOCHO already treats ontology as a first-class concept, but the repo still
describes that behavior through several adjacent pieces:

- ontology prompt/context shaping
- ontology run context metadata
- property-graph semantic overlay
- graph-model-aware indexing specs
- semantic query intent/support/evidence flow

What was still implicit is the architectural rule that user-edited ontology
must govern indexing, query, routing, tool use, and evaluation through one
compiled semantic contract.

Without that rule, indexing and query can drift into reading ontology in
different ways, which weakens both answer quality and product clarity.

## Decision

Adopt `Semantic Control Plane` as a first-class SEOCHO architecture concept.

Core rules:

- the user-authored ontology is the semantic source of truth
- JSON-LD remains the primary portable authoring format
- indexing and query must consume one compiled semantic package, not separate
  ad hoc ontology interpretations
- runtime and evaluation artifacts must record semantic package identity
- the product framing should emphasize user-owned semantic control, not opaque
  prompt engineering or vendor-managed schema state

The semantic package should carry:

- ontology/profile/glossary identity and hashes
- extraction and entity-resolution hints
- intent, slot, and relation contracts
- graph-lens and evidence policy
- tool-policy and evaluation hints

## Consequences

Positive:

- SEOCHO gets a sharper product position: edit ontology, then apply agentic
  workloads under one semantic contract
- indexing and query alignment becomes a concrete release requirement
- ontology revisions become measurable in traces and benchmarks
- users keep their semantic inputs portable while SEOCHO becomes operationally
  sticky through whole-stack alignment

Tradeoffs:

- this adds a new canonical architecture concept that must stay consistent
  across docs and future module boundaries
- the first slice is documentation and contract definition, not a full compiler
  implementation
- some target module names such as `semantic_package.py` or
  `ontology_compiler.py` remain planned architecture until landed
