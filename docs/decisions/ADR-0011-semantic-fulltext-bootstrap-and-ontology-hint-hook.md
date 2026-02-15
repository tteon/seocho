# ADR-0011: Semantic Fulltext Bootstrap And Ontology Hint Hook

- Status: Accepted
- Date: 2026-02-15
- Deciders: SEOCHO team

## Context

After introducing semantic query flow, operational gaps remained:

- semantic resolution quality depends on fulltext index availability
- entity alias/type hints from ontology governance were not consumed in runtime
- frontend users needed direct mode selection for semantic execution

## Decision

1. Add fulltext bootstrap API:
   - `POST /indexes/fulltext/ensure`
   - validates identifiers and ensures index existence per target database
2. Add ontology hint runtime hook:
   - optional file `output/ontology_hints.json`
   - supports alias normalization and label-keyword hints for reranking
3. Update Agent Studio mode selector:
   - explicit `Router`, `Debate`, `Semantic` execution modes
   - semantic mode supports workspace and database targeting

## Rationale

- fulltext availability is a hard prerequisite for semantic entity resolution
- offline ontology artifacts should influence runtime without heavy reasoning in hot path
- explicit UI mode reduces operator ambiguity and speeds debugging

## Consequences

Positive:

- deterministic semantic routing readiness checks
- better entity disambiguation on alias-heavy questions
- stronger operator visibility/control in frontend

Trade-offs:

- one additional admin/control API to maintain
- ontology hints can drift if offline artifact generation is stale

## Guardrails

- keep index operations identifier-validated and explicit
- keep ontology hints optional and fail-safe
- keep Owlready2 reasoning in offline pipeline only
