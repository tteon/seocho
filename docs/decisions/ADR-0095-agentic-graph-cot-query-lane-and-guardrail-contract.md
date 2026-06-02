# ADR-0095: Agentic Graph-CoT Query Lane and Guardrail Contract

Date: 2026-05-23
Status: Proposed

## Context

`query_mode="graph_cot"` now exists as a public semantic-query contract in the
SDK/runtime surface, but the internal execution lane is still the generic
`SemanticAgentFlow` path:

`SemanticLayer -> RouterAgent -> LPG/RDFAgent -> AnswerGenerationAgent`

That is enough to expose the mode, but not enough to guarantee the behavior
expected from Graph-CoT:

- a planner that can decide whether to continue, retry, or abstain
- a retrieval agent that returns evidence rather than prose
- an answer synthesizer that preserves missing slots
- an ontology-aware answer guardrail that can revise or refuse before final
  delivery

The codebase already has relevant pieces:

- deterministic semantic preflight in `seocho/query/semantic_flow.py`
- tiered NL->Cypher tools in `seocho/tools.py` from ADR-0090
- evidence-bundle shaping in `seocho/query/answering.py`
- ontology drift visibility via `ontology_context_mismatch` from ADR-0069

What is missing is the **typed handoff contract** and the per-agent reasoning
instructions that keep the lane bounded and auditable.

## Decision

Keep `query_mode="graph_cot"` on the same public semantic surface, but give it
a dedicated internal multi-agent lane with code-owned sequencing and typed
artifacts:

`SemanticLayer -> QuerySupervisorAgent -> Text2CypherAgent -> AnswerGenerationAgent -> AnswerGuardrailAgent -> Finalize`

The canonical design-time contracts live in:

- `seocho/query/graph_cot_contracts.py`
- `seocho/query/graph_cot_design.py`

This ADR does not require immediate runtime behavior change. It fixes the
internal interface first so the later orchestrator implementation does not need
to rediscover the lane shape.

## Lane Contract

### Orchestration ownership

Use a deterministic orchestrator, not a free-form supervisor-only loop.

- code owns stage order, retry ceiling, and final envelope shape
- the supervisor owns planning, bounded retry approval, and abstention/finalize
  judgment
- sub-agents own only their local contract

Recommended future seam:

- `seocho/query/graph_cot_flow.py`
- class name: `GraphCoTQueryOrchestrator`

### Typed artifacts

The lane uses five explicit artifacts:

1. `GraphCoTQuestionFrame`
   - deterministic SemanticLayer handoff into Graph-CoT
   - contains question, workspace, databases, entity candidates,
     unresolved entities, support preview, and ontology drift metadata
2. `SupervisorDirective`
   - planner output naming route, answer style, must-ground slots,
     no-inference zones, and retry ceiling
3. `QueryEvidencePacket`
   - retrieval output from Text2CypherAgent
   - contains Cypher, records, triples, slot fills, missing slots,
     support status, diagnostics, and ontology drift metadata
4. `AnswerDraft`
   - answer-only payload from AnswerGenerationAgent
   - contains answer text, cited facts, grounded slots, missing slots,
     abstain flag, and confidence note
5. `GuardrailVerdict`
   - answer review from AnswerGuardrailAgent
   - contains `pass|revise|refuse`, supported/unsupported claims,
     hard findings, soft findings, required repairs, and ontology flag

Supervisor finalization wraps them into `GraphCoTFinalAnswer`.

## Agent Contracts

### QuerySupervisorAgent

Reasoning role: planner

Responsibilities:

- read `GraphCoTQuestionFrame`
- decide LPG vs RDF vs hybrid vs abstain
- set `must_ground_slots` and `must_not_infer`
- approve at most one bounded retry when guardrail or execution says repair is
  justified
- refuse or downgrade to partial when hard violations remain

Rules:

- does not execute Cypher
- does not write answer prose until the guardrail verdict exists
- does not smooth over ontology mismatch, ambiguity, or temporal conflict

Inputs:

- `GraphCoTQuestionFrame`
- `ontology_context_mismatch`
- `query_diagnostics`

Output:

- `SupervisorDirective`

Handoffs:

- `Text2CypherAgent`
- `AnswerGenerationAgent`
- `AnswerGuardrailAgent`

### Text2CypherAgent

Reasoning role: retriever

Responsibilities:

- convert supervisor intent into validated read-only Cypher
- return evidence, never prose
- surface support weakness explicitly

Rules:

- always call `text2cypher`
- always call `schema_introspect`
- always call `validate_cypher` before `execute_cypher`
- permit at most one bounded repair
- include `ontology_context_mismatch` and `query_diagnostics` in the packet
- `similar_query_search` may supply few-shot context only; it is not answer
  evidence

Inputs:

- `GraphCoTQuestionFrame`
- `SupervisorDirective`

Output:

- `QueryEvidencePacket`

Required tools:

- implemented: `text2cypher`, `schema_introspect`, `validate_cypher`,
  `execute_cypher`, `similar_query_search`

### AnswerGenerationAgent

Reasoning role: synthesizer

Responsibilities:

- turn evidence into an answer draft
- preserve missing slots and abstain when support is insufficient

Rules:

- reads only `QueryEvidencePacket`
- does not retrieve new evidence
- does not fill missing facts from model prior knowledge
- cites facts already present in the packet

Input:

- `QueryEvidencePacket`

Output:

- `AnswerDraft`

### AnswerGuardrailAgent

Reasoning role: critic

Responsibilities:

- inspect the answer draft against evidence and ontology context
- classify hard vs soft findings
- request revision or refusal when the answer is not supportable

Hard finding categories:

- `ontology_violation`
- `unsupported_claim`
- `entity_ambiguity`
- `temporal_mismatch`

Soft finding category:

- `epistemic_suspicion`

Rules:

- soft suspicion can warn, but cannot add facts
- "intuition" is allowed only as a suspicion signal, never as evidence
- every `revise` verdict must include `required_repairs`

Inputs:

- `AnswerDraft`
- `QueryEvidencePacket`
- `GraphCoTQuestionFrame`

Output:

- `GuardrailVerdict`

Planned deterministic tools:

- `check_answer_support`
- `check_ontology_consistency`

## Consequences

Positive:

- public `graph_cot` mode gains a stable internal target instead of an open
  design question
- retrieval and answer generation separate cleanly; evidence can be traced and
  diffed independently from prose
- ontology drift metadata from ADR-0069 becomes actionable at answer-review
  time instead of remaining passive metadata
- later implementation can land incrementally because contracts and prompts are
  already fixed

Tradeoffs:

- more artifacts increase orchestration surface area
- guardrail review adds latency even when the answer is already acceptable
- a future guardrail that over-triggers could reduce recall unless repair rules
  stay bounded and measurable

## Implementation Notes

- shipped in this design slice:
  - `seocho/query/graph_cot_contracts.py`
  - `seocho/query/graph_cot_design.py`
  - tests in `tests/seocho/test_graph_cot_design.py`
- expected follow-up implementation seam:
  - `seocho/query/graph_cot_flow.py`
  - `runtime/server_runtime.py` and `runtime/agent_server.py` call the new
    orchestrator when `query_mode="graph_cot"`
- keep SemanticLayer deterministic and outside the LLM-agent loop
- keep Owlready2 out of request time; guardrail uses compiled ontology context
  only
