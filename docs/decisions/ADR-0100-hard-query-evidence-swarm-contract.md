# ADR-0100: Hard-Query Evidence Swarm Contract

Date: 2026-06-03

Status: Accepted

## Context

SEOCHO's debate path is useful for cross-graph or specialist comparison, but
hard query quality often fails earlier: entity resolution is partial, required
slots are unfilled, relation paths are absent, provenance is thin, or
insufficiency is hidden until answer synthesis.

Kimi-style agent swarms suggest a useful pattern, but SEOCHO should not copy
large role-based answer debates. The product layer is ontology-aligned
middleware, so the swarm should collect and verify evidence signals before the
answerer writes.

## Decision

Add an `evidence_swarm.v1` report inside `evidence_bundle.v2`.

The first slice is deterministic and contract-first. It classifies query
hardness and records scout results for:

- ontology signals
- required slot coverage
- relation path evidence
- provenance
- insufficiency

The report exposes whether the swarm path should be enabled, why the query is
hard, which scouts are on the critical path, and the recommended next step.
Runtime agent fan-out can later fill the same contract without changing the
public evidence envelope.

## Consequences

- Hard-query handling becomes inspectable before adding more orchestration.
- Debate remains a separate mode; evidence swarm is a typed evidence assembly
  layer for indexing/query improvement.
- SDK callers can read `EvidenceBundle.evidence_swarm` directly.
- Answer synthesis can consume the same typed evidence through
  `grounded_synthesis_prompt.v1`, which turns records and graph-context fallback
  into bounded evidence fragments before the LLM writes.
- Evidence-backed arithmetic uses `derived_supported`, preserving `supported:
  true` while distinguishing derived values from directly retrieved values.
- Future indexing-side swarm work can emit compatible ontology signals and
  profile candidates into the ontology control plane.

## Follow-Up

- Add true bounded parallel execution for hard query scouts.
- Feed repeated insufficiency and relation-path misses into `OntologySignal`.
- Evaluate `typed_evidence_to_answer` against `text_only`, `graph_only`, and
  `graph_text` with a fixed answerer before promoting broader swarm policies.
