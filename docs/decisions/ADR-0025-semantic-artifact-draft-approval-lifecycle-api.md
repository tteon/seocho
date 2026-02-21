# ADR-0025: Semantic Artifact Draft-Approval Lifecycle API

- Date: 2026-02-21
- Status: Accepted

## Context

Runtime ingest added `approved_only` policy but lacked a first-class workflow for
managing semantic artifacts (ontology/SHACL candidates) between extraction and
production application.

## Decision

1. Add semantic artifact lifecycle APIs:
   - `POST /semantic/artifacts/drafts`
   - `GET /semantic/artifacts`
   - `GET /semantic/artifacts/{artifact_id}`
   - `POST /semantic/artifacts/{artifact_id}/approve`
2. Store artifacts with explicit status (`draft` or `approved`) and reviewer metadata.
3. Extend runtime ingest:
   - support `approved_artifact_id` for server-side approved artifact resolution
   - keep existing inline `approved_artifacts` override contract
4. Enforce dedicated permission action:
   - `manage_semantic_artifacts`

## Consequences

Positive:

- clear governance workflow (draft -> review -> approved apply)
- safer `approved_only` rollout path without manual payload copy
- better auditability for semantic artifact promotion

Tradeoffs:

- additional API/storage surface area
- requires artifact lifecycle operations in deployment/runbooks

## Implementation Notes

Key files:

- `extraction/semantic_artifact_store.py`
- `extraction/semantic_artifact_api.py`
- `extraction/agent_server.py`
- `extraction/policy.py`
- tests under `extraction/tests/`
