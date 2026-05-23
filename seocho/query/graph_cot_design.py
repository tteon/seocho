from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Tuple


def _query_context(ontology: Any) -> Dict[str, Any]:
    if ontology is None or not hasattr(ontology, "to_query_context"):
        return {}
    context = ontology.to_query_context()
    return context if isinstance(context, dict) else {}


@dataclass(frozen=True, slots=True)
class GraphCoTToolSpec:
    """Declarative tool spec for one Graph-CoT agent."""

    name: str
    purpose: str
    io_contract: str
    status: Literal["implemented", "planned"] = "implemented"


@dataclass(frozen=True, slots=True)
class GraphCoTAgentSpec:
    """Reasoning and tool contract for a Graph-CoT sub-agent."""

    name: str
    reasoning_role: Literal["planner", "retriever", "synthesizer", "critic"]
    responsibility: str
    instructions: str
    inputs: Tuple[str, ...]
    output_contract: str
    required_handoffs: Tuple[str, ...] = field(default_factory=tuple)
    required_tools: Tuple[GraphCoTToolSpec, ...] = field(default_factory=tuple)
    optional_tools: Tuple[GraphCoTToolSpec, ...] = field(default_factory=tuple)


def graph_cot_supervisor_system_prompt(ontology: Any) -> str:
    ctx = _query_context(ontology)
    return f"""You are QuerySupervisorAgent for SEOCHO Graph-CoT query mode.
Your job is to plan, judge, and finalize a graph-grounded answering run.

**Ontology: {ctx.get('ontology_name', 'unknown')}**

{ctx.get('graph_schema', '')}

## Inputs

- GraphCoTQuestionFrame from the deterministic SemanticLayer
- ontology/profile drift metadata
- prior retrieval failures or query diagnostics

## Workflow

1. Read the GraphCoTQuestionFrame and emit a SupervisorDirective.
2. Decide whether the run should continue with LPG, RDF, hybrid, or abstain.
3. Hand off retrieval to Text2CypherAgent.
4. Hand off evidence-only output to AnswerGenerationAgent.
5. Hand off the answer draft and evidence to AnswerGuardrailAgent.
6. If the verdict is `revise`, allow at most one bounded retry and name the exact repair.
7. Finalize as GraphCoTFinalAnswer only after guardrail review.

## Rules

- Never answer from model memory when evidence is missing.
- Treat ontology mismatch, entity ambiguity, and temporal conflicts as first-class failure causes.
- Do not generate Cypher or prose evidence yourself; you are the planner and judge.
- Prefer partial or abstained answers over unsupported synthesis.
- The final answer must preserve missing slots instead of hiding them.
"""


def graph_cot_text2cypher_system_prompt(ontology: Any) -> str:
    ctx = _query_context(ontology)
    return f"""You are Text2CypherAgent for SEOCHO Graph-CoT query mode.
Your job is to turn a supervisor directive into a validated query and a QueryEvidencePacket.

**Ontology: {ctx.get('ontology_name', 'unknown')}**

{ctx.get('graph_schema', '')}

## Required workflow

1. Read GraphCoTQuestionFrame and SupervisorDirective.
2. Call `text2cypher` to build the first structured Cypher plan.
3. Call `schema_introspect` so the live workspace schema remains the source of truth.
4. Call `validate_cypher` before every `execute_cypher`.
5. Call `execute_cypher` and capture records, diagnostics, and ontology mismatch metadata.
6. If validation or execution fails, perform at most one bounded repair.
7. Return QueryEvidencePacket only.

## Rules

- Do not produce prose answers.
- Never invent labels, relationships, or properties not present in schema_introspect output.
- Include `slot_fills`, `grounded_slots`, `missing_slots`, `query_diagnostics`, and `ontology_context_mismatch`.
- If support is weak or partial, say that in the packet instead of smoothing it over.
- `similar_query_search` may be used as few-shot support, never as answer evidence.
"""


def graph_cot_answer_generation_system_prompt() -> str:
    return """You are AnswerGenerationAgent for SEOCHO Graph-CoT query mode.
Your job is to turn QueryEvidencePacket into AnswerDraft.

## Required workflow

1. Read QueryEvidencePacket only.
2. Build the answer from `slot_fills`, `selected_triples`, and grounded records.
3. Name missing_slots and unresolved_entities explicitly when they matter.
4. Abstain when the packet does not support the requested claim.
5. Return AnswerDraft only.

## Rules

- Do not retrieve new evidence.
- Do not fill missing information from model prior knowledge.
- Keep cited facts tied to evidence already present in the packet.
- Prefer a short grounded answer over a broad speculative one.
"""


def graph_cot_answer_guardrail_system_prompt(ontology: Any) -> str:
    ctx = _query_context(ontology)
    return f"""You are AnswerGuardrailAgent for SEOCHO Graph-CoT query mode.
Your job is to review AnswerDraft against QueryEvidencePacket and ontology context, then emit GuardrailVerdict.

**Ontology: {ctx.get('ontology_name', 'unknown')}**

{ctx.get('graph_schema', '')}

## Review checks

- ontology_violation: wrong entity type, relation type, or ontology drift conflict
- unsupported_claim: answer text says more than the packet supports
- entity_ambiguity: multiple plausible referents remain unresolved
- temporal_mismatch: answer mixes time scopes or states unsupported recency
- epistemic_suspicion: the wording feels like an inference leap even if hard evidence is absent

## Rules

- Hard findings may lead to `revise` or `refuse`.
- Soft suspicion may warn, but it must not add facts or replace evidence.
- Your intuition is allowed only as a suspicion signal, never as support.
- Every revise decision must include required_repairs.
- If the answer is acceptable, return `pass` with the supported claims named explicitly.
"""


def build_graph_cot_agent_specs(ontology: Any) -> Dict[str, GraphCoTAgentSpec]:
    """Return the canonical design-time agent specs for Graph-CoT query mode."""

    return {
        "QuerySupervisorAgent": GraphCoTAgentSpec(
            name="QuerySupervisorAgent",
            reasoning_role="planner",
            responsibility="Plan the lane, approve retries, and finalize only after guardrail review.",
            instructions=graph_cot_supervisor_system_prompt(ontology),
            inputs=("GraphCoTQuestionFrame", "ontology_context_mismatch", "query_diagnostics"),
            output_contract="SupervisorDirective",
            required_handoffs=(
                "Text2CypherAgent",
                "AnswerGenerationAgent",
                "AnswerGuardrailAgent",
            ),
        ),
        "Text2CypherAgent": GraphCoTAgentSpec(
            name="Text2CypherAgent",
            reasoning_role="retriever",
            responsibility="Generate validated read-only Cypher and return evidence, never prose.",
            instructions=graph_cot_text2cypher_system_prompt(ontology),
            inputs=("GraphCoTQuestionFrame", "SupervisorDirective"),
            output_contract="QueryEvidencePacket",
            required_tools=(
                GraphCoTToolSpec(
                    name="text2cypher",
                    purpose="Build deterministic Cypher from structured intent.",
                    io_contract="intent -> {cypher, params}",
                ),
                GraphCoTToolSpec(
                    name="schema_introspect",
                    purpose="Read the live workspace schema before validation and execution.",
                    io_contract="database -> {labels, relationship_types, property_keys}",
                ),
                GraphCoTToolSpec(
                    name="validate_cypher",
                    purpose="Apply allow-list and safety validation before execution.",
                    io_contract="cypher + allow-lists -> {ok, violations}",
                ),
                GraphCoTToolSpec(
                    name="execute_cypher",
                    purpose="Execute the validated read-only query and capture ontology drift metadata.",
                    io_contract="cypher + params -> {records, ontology_context_mismatch}",
                ),
                GraphCoTToolSpec(
                    name="similar_query_search",
                    purpose="Retrieve past validated NL->Cypher examples as bounded few-shot context.",
                    io_contract="question -> {examples}",
                ),
            ),
        ),
        "AnswerGenerationAgent": GraphCoTAgentSpec(
            name="AnswerGenerationAgent",
            reasoning_role="synthesizer",
            responsibility="Turn evidence into an answer draft while preserving missing information.",
            instructions=graph_cot_answer_generation_system_prompt(),
            inputs=("QueryEvidencePacket",),
            output_contract="AnswerDraft",
        ),
        "AnswerGuardrailAgent": GraphCoTAgentSpec(
            name="AnswerGuardrailAgent",
            reasoning_role="critic",
            responsibility="Review answer grounding against ontology and evidence; revise or refuse when needed.",
            instructions=graph_cot_answer_guardrail_system_prompt(ontology),
            inputs=("AnswerDraft", "QueryEvidencePacket", "GraphCoTQuestionFrame"),
            output_contract="GuardrailVerdict",
            required_tools=(
                GraphCoTToolSpec(
                    name="check_answer_support",
                    purpose="Deterministically compare draft claims with cited facts and slot fills.",
                    io_contract="draft + packet -> {supported_claims, unsupported_claims}",
                    status="planned",
                ),
                GraphCoTToolSpec(
                    name="check_ontology_consistency",
                    purpose="Check entity/relation/type consistency against ontology context and drift metadata.",
                    io_contract="draft + packet + ontology -> {hard_findings, soft_findings}",
                    status="planned",
                ),
            ),
        ),
    }


__all__ = [
    "GraphCoTAgentSpec",
    "GraphCoTToolSpec",
    "build_graph_cot_agent_specs",
    "graph_cot_answer_generation_system_prompt",
    "graph_cot_answer_guardrail_system_prompt",
    "graph_cot_supervisor_system_prompt",
    "graph_cot_text2cypher_system_prompt",
]
