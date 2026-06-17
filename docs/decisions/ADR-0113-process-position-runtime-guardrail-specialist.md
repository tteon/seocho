# ADR-0113: Process-Position Runtime Guardrail Specialist

Date: 2026-06-15
Status: Accepted

## Context

The expanded AMI context-graph guardrail experiment compared vector content,
pure context graph, and content-plus-context-graph hybrid answering over
decision meeting slices. The large-N run produced 1,545 judged answers across
131 completed threads.

Aggregate paired results showed:

- pure graph reached parity with vector overall
- hybrid beat vector overall
- graph was strongest on factual and decision-summary slices
- hybrid was strongest on decision/action slices

This means the process-position graph should not replace vector retrieval
globally. Its product value is as an ontology-governed runtime specialist that
adds who/when/where/how decision structure when the question asks about
decisions, outcomes, assignments, or action items.

## Decision

SEOCHO runtime route profiles will expose a `process_position` guardrail
specialist for decision/action process queries.

The specialist is advisory runtime metadata, not a heavy ontology reasoner:

- intent detection maps decision/action questions to
  `decision_process_lookup`
- the route class is `R5_LONG_CONTEXT_REASONING`
- the recommended retrieval lane is `hybrid`
- route metadata records the process-position profile and experiment-backed
  expected lift
- the answerability gate reads already-compiled relationship metadata from the
  semantic layer; it does not load ontology files or run Owlready2 in request
  paths

The runtime will continue to use vector as the cheap default lookup lane and
will use pure graph only for certified deterministic graph serving when the
answerability gate says the required relation is declared.

## Consequences

Positive:

- Product runtime now carries the experiment result as a traceable routing
  contract instead of leaving it in the benchmark harness.
- Decision/action questions can opt into content plus ontology-governed
  process graph context.
- Operators can inspect the selected guardrail profile in semantic context and
  evidence bundles.
- The change preserves `workspace_id` and keeps heavy ontology governance out
  of the hot path.

Tradeoffs:

- The first runtime slice surfaces the routing/profile decision; it does not yet
  force every deployed graph to use a specific approved artifact automatically.
- Runtime quality still depends on approved artifacts being available for the
  target workspace/database.
- Follow-up work should wire approved artifact selection and Opik trace facets
  into operator-facing dashboards.

## Implementation Notes

- Runtime policy: `seocho/query/route_policy.py`
- Intent and evidence bundle surface: `seocho/query/intent.py`
- Semantic flow surface: `seocho/query/semantic_flow.py`
- Tests: `seocho/tests/test_route_policy.py`,
  `seocho/tests/test_semantic_query_phase_d.py`
