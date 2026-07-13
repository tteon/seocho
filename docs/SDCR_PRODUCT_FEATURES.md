# SDCR product features

This document translates the FinDER study into runtime features. It is an
implementation boundary, not a claim that every research component belongs in
the request hot path.

## Runtime path

1. The query planner extracts answer slots and an issuer/period scope.
2. The capability registry maps slots to authorized graph views.
3. The conservative router selects one view first and adds a specialist only
   for an uncovered slot or a verified evidence conflict.
4. The evidence filter removes protected fields and preserves provenance.
5. The conflict verifier reconciles incompatible values before synthesis.
6. A decision receipt records the policy decision, selected views, evidence
   references, authorization result, and fallback outcome.

Every stage carries `workspace_id`; authorization remains an external runtime
policy, and ontology reasoning stays offline.

## Delivery boundary

The first implementation should reuse the existing semantic flow, strategy
chooser, evidence bundle, and run registry. New behavior must be additive:
existing semantic and debate routes remain valid when no capability metadata is
available. LiteLLM cost and latency callbacks belong in observability, not in
the routing decision itself.

The following are deliberately deferred: GNN routing, PageRank-only routing,
full OWL entailment in the hot path, and automatic replacement of human labels
with LLM judgments.

The first policy slice is implemented in `seocho.query.sdcr`. It is pure and
deterministic: database adapters and model calls provide capabilities and
evidence, while the policy module owns coalition selection, protected-field
filtering, conflict detection, and the serializable decision receipt.

On the `main` runtime line, OpenTelemetry is the canonical integration point
and Grafana/Prometheus are the operational consumers. The LiteLLM usage
adapter must emit OTEL span attributes and low-cardinality metrics rather than
introduce a second trace protocol. The existing `StageTimer` remains the
local timing helper; Opik, Langfuse, and Phoenix are optional exporters only.

Recommended metrics are `seocho_agent_calls_total`,
`seocho_agent_latency_ms`, `seocho_llm_tokens_total`,
`seocho_llm_cost_usd`, and `seocho_sdcr_route_total`. Keep
`workspace_id`, query text, and source IDs out of metric labels; attach them to
redacted spans or decision receipts to avoid high-cardinality and leakage.
