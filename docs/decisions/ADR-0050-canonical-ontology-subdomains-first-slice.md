# ADR-0050: Canonical Ontology Subdomains First Slice

- Status: Accepted
- Date: 2026-04-13

## Context

`seocho/ontology.py` had grown into a single module that handled:

- the public ontology schema model
- canonical JSON-LD persistence
- runtime artifact promotion
- semantic prompt context generation
- governance-adjacent helpers

That made ontology behavior powerful, but it also made the ontology layer hard
to share cleanly across local SDK and runtime paths. The canonicalization work
for query and agent modules needs the ontology layer to expose similarly clear
subdomains.

## Decision

Keep `Ontology` as the stable public facade, but split internal ontology
responsibilities into explicit helper modules:

- `seocho/ontology_serialization.py`
  - JSON-LD loading and persistence
- `seocho/ontology_artifacts.py`
  - runtime artifact promotion and typed semantic prompt context generation
- `seocho/ontology_governance.py`
  - offline governance path remains separate

`Ontology` delegates to those helpers while preserving the existing public API.

## Consequences

### Positive

- internal ontology boundaries are explicit
- runtime artifact generation no longer depends on ad hoc client glue
- local SDK and runtime parity work has a cleaner ontology-side contract
- follow-on refactors can move faster without forcing a public API rewrite

### Negative

- helper modules introduce more internal files to maintain
- some implementation duplication may remain until later ontology slices move
  more responsibilities behind the same boundaries

## Follow-up

- continue shrinking `seocho/ontology.py` toward facade-only responsibilities
- align runtime ingestion and server artifact promotion on the same ontology
  helper contracts
