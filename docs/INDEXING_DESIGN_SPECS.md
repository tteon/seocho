# Indexing Design Specs

SEOCHO can load a YAML indexing design spec and turn it into a local SDK
construction contract:

- the graph model is explicit: `lpg`, `rdf`, or `hybrid`
- the storage target is explicit: `ladybug`, `neo4j`, or `dozerdb`
- the ontology binding is required
- indexing defaults such as validation, provenance, and inquiry mode become
  reviewable code

## Why

Use indexing design specs when you want:

- graph-model-aware ingestion checked into git
- RDF- and LPG-specific indexing behavior without forked SDK code
- an explicit ontology slot for every ingestion design
- a bounded reasoning cycle for anomaly-driven repair and analyst review

If the YAML omits the top-level `ontology:` section, or the `ontology:` section
does not declare a binding such as `profile`, `ontology_id`, `package_id`, or
`path`, SEOCHO raises a `ValueError`.

## Core Shape

```yaml
name: hybrid-finance-inquiry
graph_model: hybrid
storage_target: neo4j
ontology:
  required: true
  profile: finance-core
ingestion:
  extraction_strategy: domain
  linking_strategy: llm
  validation_on_fail: reject
  inference_mode: deductive
materialization:
  rdf_mode: neo4j_labels
  metric_model: node
  provenance_mode: full
reasoning_cycle:
  enabled: true
  anomaly_sources:
    - shacl_violation
    - unsupported_answer
  abduction:
    mode: candidate_only
  deduction:
    require_testable_predictions: true
  induction:
    require_support_assessment: true
  promotion:
    analyst_approval_required: true
constraints:
  require_workspace_id: true
```

## Inquiry Cycle Contract

SEOCHO treats abduction, deduction, and induction as a bounded inquiry loop,
not as competing alternatives:

1. anomaly detection
2. abduction to propose a candidate explanation
3. deduction to derive testable graph/document predictions
4. induction to verify those predictions against graph, text, or tables

Important guardrail:

- abductive output is candidate-only by default
- it should be annotated, not silently promoted to canonical fact
- analyst approval or explicit verification should be required before
  promotion into stable fact layers

## LPG Prompt Shaping

For `graph_model: lpg`, SEOCHO installs a property-graph-oriented extraction
prompt by default:

- stable scalar attributes stay as node/edge properties
- repeated or period-specific metrics stay as separate nodes
- provenance-friendly properties such as `source_span`, `period`,
  `confidence`, and `extractor_confidence` are encouraged in the output

This makes LPG ingestion use property-graph strengths instead of forcing every
fact through an RDF-like shape and flattening it afterward.

## Build A Client

```python
from seocho import Ontology, Seocho

onto = Ontology.from_jsonld("schema.jsonld")

client = Seocho.from_indexing_design(
    "examples/indexing_designs/lpg_finance_provenance.yaml",
    ontology=onto,
    llm="openai/gpt-4o-mini",
    workspace_id="finance-prod",
)
```

For `storage_target: neo4j` or `dozerdb`, pass a Bolt graph target:

```python
client = Seocho.from_indexing_design(
    "examples/indexing_designs/rdf_deductive_finance.yaml",
    ontology=onto,
    llm="openai/gpt-4o-mini",
    graph="bolt://localhost:7687",
    workspace_id="finance-prod",
)
```

## Included Examples

- [lpg_finance_provenance.yaml](/tmp/seocho-land-finder-e2e/examples/indexing_designs/lpg_finance_provenance.yaml)
- [rdf_deductive_finance.yaml](/tmp/seocho-land-finder-e2e/examples/indexing_designs/rdf_deductive_finance.yaml)
- [hybrid_inquiry_finance.yaml](/tmp/seocho-land-finder-e2e/examples/indexing_designs/hybrid_inquiry_finance.yaml)
