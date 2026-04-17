# ADR-0084: Typed Query Contract For Runtime And Semantic Paths

## Status

Accepted

## Context

ADR-0083 moved runtime graph reads and Cypher execution behind `QueryProxy`,
but two incompatible query contracts still existed:

- canonical `GraphStore` paths returned typed `list[dict]` rows
- legacy connector paths returned JSON strings or `"Error ..."` strings

The semantic query stack still parsed connector JSON directly and degraded any
contract or execution failure into an empty list. That made three different
states look the same:

- there are no matching graph records
- the connector returned a malformed payload
- the connector/query itself failed

For FinDER and private finance regression work, that ambiguity blocks useful
diagnosis and hides ontology/query drift behind false "no data" symptoms.

## Decision

SEOCHO will keep the legacy string-returning connector API for compatibility,
but the canonical read/query seam is now a typed row/error contract.

Rules:

1. `seocho.query.query_proxy` owns the shared row coercion helper and
   `QueryExecutionError`.
2. Query-facing code should normalize payloads through that shared seam instead
   of calling `json.loads(...)` inline.
3. `extraction.graph_connector.MultiGraphConnector` exposes a typed `query()`
   method while preserving `run_cypher()` for compatibility.
4. Semantic query flows must surface query-contract failures in diagnostics and
   reasoning traces rather than silently flattening them into "no records".
5. Finance benchmark diagnosis should classify query-contract failures under a
   dedicated query code.

## Consequences

Positive:

- runtime and semantic query paths now share one row/error contract
- FinDER-style regressions can distinguish connector failures from empty graph
  answers
- legacy connector compatibility remains intact while canonical code moves to
  typed rows

Negative:

- runtime and semantic code still carry compatibility logic until direct
  `run_cypher()` callers are fully removed
- some query call sites still degrade failures to fallback behavior after
  recording diagnostics, so not every caller becomes fail-fast immediately

## Implementation Notes

- typed row coercion: `seocho/query/query_proxy.py`
- compatibility connector surface: `extraction/graph_connector.py`
- semantic diagnostics: `seocho/query/semantic_agents.py`
- semantic flow exposure: `seocho/query/semantic_flow.py`
- finance diagnosis split: `seocho/benchmarking.py`
