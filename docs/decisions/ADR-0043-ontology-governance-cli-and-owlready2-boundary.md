# ADR-0043: Ontology Governance CLI And Owlready2 Boundary

Date: 2026-04-12
Status: Accepted

## Context

The public SDK README described ontology behavior in a way that was broadly
correct but too absolute in a few places:

- query-time prompting is schema-aware, but not every path uses the exact same
  full-schema prompt on the first pass
- constraint statements are generated and can be applied, but they are not
  automatically applied on every indexing call
- low-quality ontology-guided re-extraction exists, but it is opt-in through
  quality thresholds and retries

At the same time, ontology operations such as validation, diffing, SHACL export,
and OWL inspection were spread across runtime helpers and scripts without one
clear developer-facing governance surface.

The project also already had an explicit boundary: Owlready2 is allowed only in
the offline ontology governance path, not in the request hot path.

## Decision

SEOCHO will keep ontology runtime behavior lightweight and explicit, and add a
small offline governance CLI around that runtime contract.

The SDK now exposes:

- `seocho ontology check --schema ...`
- `seocho ontology export --schema ... --format {jsonld,yaml,dict,shacl}`
- `seocho ontology diff --left ... --right ...`
- `seocho ontology inspect-owl --source ...`

These commands are backed by a new offline governance helper module:

- `seocho/ontology_governance.py`

Owlready2 remains optional and offline-only:

- packaged behind optional extra `seocho[ontology]`
- used only for explicit OWL inspection/governance commands
- not used in indexing/query request paths

README wording will be tightened so ontology claims reflect actual runtime
behavior instead of implying stronger automation than currently exists.

## Consequences

Positive:

- ontology lifecycle claims become more precise and easier to trust
- developers get one clear CLI surface for schema validation, SHACL export, and
  version-to-version comparison
- Owlready2 stays useful for governance without leaking heavy reasoning into the
  runtime hot path

Tradeoffs:

- governance remains intentionally lightweight; this is not a full ontology
  migration framework
- `inspect-owl` is a helper/inspection path, not a complete OWL compiler
- operators still need an explicit call to apply generated Neo4j constraints

## Implementation Notes

- governance helpers: `seocho/ontology_governance.py`
- CLI surface: `seocho/cli.py`
- package extra: `pyproject.toml`
- wording updates: `README.md`, `docs/PYTHON_INTERFACE_QUICKSTART.md`,
  `docs/ARCHITECTURE.md`
