# ADR-0022: SHACL Artifact Export and Batch Rule Aggregation

- Date: 2026-02-21
- Status: Accepted

## Context

Rule governance required two practical gaps to be closed:

1. inferred rules were exportable to Cypher constraints, but not to SHACL-compatible artifacts
2. runtime raw ingest inferred constraints per-record, which weakened rule quality when multiple raw materials were provided together

## Decision

1. Add SHACL-compatible export API:
   - `POST /rules/export/shacl`
   - returns shape JSON and Turtle artifact text from a rule profile
2. Keep Cypher export path unchanged:
   - `POST /rules/export/cypher`
3. Upgrade runtime raw ingest rule flow:
   - aggregate extracted graph fragments across batch records
   - infer one batch-level rule profile
   - apply that shared profile to each record graph before load
   - return `rule_profile` in ingest response payload for auditability

## Consequences

Positive:

- governance artifacts now support both DozerDB constraint rollout and SHACL-aligned downstream use
- multi-record raw ingest produces more stable inferred constraints
- users can inspect inferred batch rules directly from runtime ingest results

Tradeoffs:

- ingest path adds one extra aggregation/inference step
- SHACL export remains compatibility-focused and not a full closed-world validator

## Implementation Notes

Key files:

- `extraction/rule_export.py`
- `extraction/rule_api.py`
- `extraction/agent_server.py`
- `extraction/runtime_ingest.py`
- `extraction/tests/test_rule_export.py`
- `extraction/tests/test_rule_api.py`
- `extraction/tests/test_runtime_ingest.py`
