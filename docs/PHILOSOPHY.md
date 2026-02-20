# SEOCHO Philosophy Charter

This document captures the non-negotiable design philosophy for SEOCHO and how it maps to implementation.

## Core Philosophy

1. Extract domain rules and key domain characteristics from heterogeneous data into a SHACL-like semantic layer.
2. Preserve extracted data in table form and build ontology artifacts (`.ttl` and related files) as merge/governance evidence.
3. Use ontology-aware entity extraction and linking by providing `prompt + ontology` context to LLMs, then convert related data into graph structures.
4. Map graph instances to graph agents in a 1:1 model.
5. Keep router agent as the default request entrypoint and delegate to graph agents that can answer the query.
6. Use supervisor-style orchestration for router <-> graph-agent collaboration, with ontology metadata driving query-to-instance allocation.
7. Track and manage all agent-layer flow data with Opik.

## Additional Perspectives

- Provenance-first evidence model:
  - Every extracted fact, inferred rule, and routing decision must be traceable to source records/chunks.
- Confidence-aware decisioning:
  - Semantic disambiguation and routing should expose confidence and support deterministic human override.
- Contract-first DAG integration:
  - Backend emits strict topology metadata (`node_id`, `parent_id`, `parent_ids`) for frontend workflow canvas rendering.
  - UI consumes backend topology directly and must avoid heuristic-only lineage reconstruction.
- Practical readiness before promotion:
  - Rule profiles must pass readiness checks (`/rules/assess`) before governance promotion/export.
- Versioned ontology lifecycle:
  - Ontology files (`.ttl`, hint artifacts, profiles) are control-plane assets with explicit versioning and rollback.
- Observability as a product surface:
  - Opik traces are not only debugging artifacts; they are operating evidence for trust and governance.

## Implementation Mapping

- Ontology-guided extraction/linking:
  - `extraction/ontology_prompt_bridge.py`
  - `extraction/extractor.py`
  - `extraction/linker.py`
- SHACL-like rule lifecycle:
  - `extraction/rule_constraints.py`
  - `extraction/rule_api.py`
  - `extraction/rule_export.py`
- Graph instance and agent mapping:
  - `extraction/database_manager.py`
  - `extraction/agent_factory.py`
  - `extraction/debate.py`
- Router/supervisor and semantic orchestration:
  - `extraction/agent_server.py`
  - `extraction/semantic_query_flow.py`
- Flow telemetry and auditability:
  - `extraction/tracing.py`
  - Opik profile in `docker-compose.yml`

## Operating Checks

Before promotion to production-like use:

1. Rules:
   - Run `/rules/assess` and review `practical_readiness`, `violation_breakdown`, `export_preview`.
2. Routing:
   - Confirm router decisions align with ontology-backed graph metadata.
3. Traceability:
   - Verify Opik spans include enough metadata to reconstruct decision path.
4. UI contract:
   - Ensure workflow canvas lineage uses backend topology fields, not inferred-only links.
5. Governance:
   - Persist ontology/rule artifacts as versioned files and record change rationale in decision docs.
