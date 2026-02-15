# ADR-0012: Semantic Override Loop And Offline Owlready2 Hints

- Status: Accepted
- Date: 2026-02-15
- Deciders: SEOCHO team

## Context

Semantic routing now resolves candidates, but operators still need:

- manual correction when top candidate is wrong
- a reproducible offline path to generate ontology alias/type hints

## Decision

1. Add semantic override contract:
   - `POST /run_agent_semantic` accepts `entity_overrides`
   - override candidates are injected at top rank and tracked in trace metadata
2. Add Agent Studio override loop:
   - semantic mode displays top candidates per extracted entity
   - user selection triggers re-run with fixed mappings
3. Add offline owlready2 hint builder:
   - `scripts/ontology/build_ontology_hints.py`
   - outputs `output/ontology_hints.json` consumed by runtime resolver

## Rationale

- manual pinning closes the final precision gap for ambiguous entities
- offline hint generation keeps hot-path light while improving reranking quality
- loop is operator-friendly and auditable

## Consequences

Positive:

- better semantic routing precision for hard disambiguation cases
- faster debugging with explicit candidate pinning
- reusable ontology hint artifacts from governance pipeline

Trade-offs:

- additional UI complexity in semantic mode
- hint quality depends on ontology annotation completeness

## Guardrails

- overrides must reference allowed target databases
- runtime keeps read-safe behavior
- owlready2 dependency remains offline-only
