Date: 2026-06-03
Status: Accepted

# ADR-0098: Experiment-Backed Route Profile and Answer Shape Contract

## Context

ICML 2026 FinDER experiments and the KDD Cup 2026 DataAgent-Bench semantic-graph
lane converged on the same operational lesson:

- raw text remains the safest substrate for exact numeric and qualifier fidelity
- graph context helps most when it narrows entities, relations, joins, and slots
- generic planner/ReAct loops can underperform direct or typed paths
- adding graph/tool context without visible insufficiency creates plausible but
  weakly grounded answers

SEOCHO already has intent support assessment, missing-slot tracking, typed
query execution, and Graph-CoT guardrails. The missing contract was a compact
answer-path profile that travels with the evidence bundle so answer synthesis,
tracing, and SDK callers can see how the runtime intends to use graph/text
evidence before the final response is written.

## Decision

Extend the shared `evidence_bundle.v2` payload with:

- `route_profile`: a lightweight `route_profile.v1` object containing
  `route_class`, `question_determinism`, `tool_policy`, `recommended_tools`,
  and `rationale`
- `answer_shape`: a compact expected output shape such as `count_scalar`,
  `scalar_metric`, `ranked_projection`, `relationship_summary`,
  `evidence_summary`, or `partial_evidence_summary`
- `answer_shape_profile`: an inspectable `answer_shape.v1` payload with the
  route class, determinism, and rationale

The first implementation is deterministic and deliberately small. It does not
replace the existing semantic router, strategy chooser, or Graph-CoT lane.
Instead, it annotates the evidence bundle produced by `seocho.query.intent` and
preserves the fields in the SDK `EvidenceBundle` model.

## Consequences

- Runtime and SDK callers can distinguish graph-join, long-context evidence,
  and simple lookup cases without inferring that from opaque traces.
- Missing slots remain visible alongside the expected answer shape, preventing
  answer synthesis from silently treating partial graph evidence as complete.
- The contract creates a narrow landing point for later insufficiency-gated
  fallback ladders: graph evidence can trigger text expansion, vector fallback,
  or conservative partial answers based on route profile and missing slots.
- The taxonomy stays intentionally smaller than the KDD competition system; task
  specific bridges and regexes are not imported into SEOCHO.

## Follow-up

- Make `QueryAnswerSynthesizer` consume `answer_shape` directly for deterministic
  table/scalar/relationship rendering.
- Add a route-profile trace field to semantic runtime run metadata.
- Evaluate `text_only`, `graph_only`, `graph_text`, and `slot_bundle` with a
  fixed answerer before promoting new graph-retrieval policies.
