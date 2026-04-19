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

When a local `add()` run hits a SHACL rejection, fallback extraction, or write
error under an enabled `reasoning_cycle`, SEOCHO records a compact
`metadata.reasoning_cycle` report on the returned `Memory` object. That report
stays in the anomaly phase and points the next step at abduction; it does not
promote any candidate inference to fact.

The same contract can now be supplied to semantic and debate query paths. When
support is `partial` or `unsupported`, SEOCHO returns a compact top-level
`reasoning_cycle` report in the semantic/debate response so the caller can
decide whether to escalate into analyst review, additional tool use, or a
separate inquiry pass.

The same payload also flows through `client.plan(...).with_reasoning_cycle(...).run()`
and `client.platform_chat(..., reasoning_cycle=...)`, so SDK plans and UI
chat surfaces can share the same inquiry contract.

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

## Query With The Same Inquiry Contract

```python
reasoning_cycle = {
    "enabled": True,
    "anomaly_sources": ["unsupported_answer", "query_diagnostic"],
    "abduction": {"mode": "candidate_only"},
    "deduction": {"require_testable_predictions": True},
    "induction": {"require_support_assessment": True},
    "promotion": {"analyst_approval_required": True},
}

semantic = client.semantic(
    "What drove NVIDIA's gross margin expansion?",
    databases=["finderrt20260417d"],
    reasoning_cycle=reasoning_cycle,
)

if semantic.reasoning_cycle:
    print(semantic.reasoning_cycle["status"])
    print(semantic.reasoning_cycle["observed_anomalies"])

debate = client.debate(
    "Compare Tesla deliveries year over year.",
    graph_ids=["finderrt20260417d"],
    reasoning_cycle=reasoning_cycle,
)
```

## Included Examples

- [lpg_finance_provenance.yaml](/tmp/seocho-land-finder-e2e/examples/indexing_designs/lpg_finance_provenance.yaml)
- [rdf_deductive_finance.yaml](/tmp/seocho-land-finder-e2e/examples/indexing_designs/rdf_deductive_finance.yaml)
- [hybrid_inquiry_finance.yaml](/tmp/seocho-land-finder-e2e/examples/indexing_designs/hybrid_inquiry_finance.yaml)
