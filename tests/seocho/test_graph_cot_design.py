from seocho.query import (
    AnswerDraft,
    GraphCoTFinalAnswer,
    GraphCoTQuestionFrame,
    GuardrailFinding,
    GuardrailVerdict,
    QueryEvidencePacket,
    build_graph_cot_agent_specs,
)


class FakeOntology:
    def to_query_context(self):
        return {
            "ontology_name": "test-graph",
            "graph_schema": "(:Company)-[:ACQUIRED]->(:Company)",
        }


def test_graph_cot_agent_specs_define_expected_handoffs_and_tools() -> None:
    specs = build_graph_cot_agent_specs(FakeOntology())

    assert set(specs) == {
        "QuerySupervisorAgent",
        "Text2CypherAgent",
        "AnswerGenerationAgent",
        "AnswerGuardrailAgent",
    }

    supervisor = specs["QuerySupervisorAgent"]
    assert supervisor.reasoning_role == "planner"
    assert supervisor.required_handoffs == (
        "Text2CypherAgent",
        "AnswerGenerationAgent",
        "AnswerGuardrailAgent",
    )
    assert supervisor.output_contract == "SupervisorDirective"

    text2cypher = specs["Text2CypherAgent"]
    assert text2cypher.reasoning_role == "retriever"
    assert {tool.name for tool in text2cypher.required_tools} == {
        "text2cypher",
        "schema_introspect",
        "validate_cypher",
        "execute_cypher",
        "similar_query_search",
    }
    assert all(tool.status == "implemented" for tool in text2cypher.required_tools)

    guardrail = specs["AnswerGuardrailAgent"]
    assert {tool.name for tool in guardrail.required_tools} == {
        "check_answer_support",
        "check_ontology_consistency",
    }
    assert all(tool.status == "planned" for tool in guardrail.required_tools)


def test_graph_cot_instructions_enforce_grounding_and_guardrail_rules() -> None:
    specs = build_graph_cot_agent_specs(FakeOntology())

    supervisor_prompt = specs["QuerySupervisorAgent"].instructions
    assert "Never answer from model memory" in supervisor_prompt
    assert "at most one bounded retry" in supervisor_prompt

    text2cypher_prompt = specs["Text2CypherAgent"].instructions
    assert "Do not produce prose answers." in text2cypher_prompt
    assert "validate_cypher" in text2cypher_prompt
    assert "ontology_context_mismatch" in text2cypher_prompt

    answer_prompt = specs["AnswerGenerationAgent"].instructions
    assert "missing_slots" in answer_prompt
    assert "Do not retrieve new evidence." in answer_prompt

    guardrail_prompt = specs["AnswerGuardrailAgent"].instructions
    assert "Soft suspicion may warn, but it must not add facts" in guardrail_prompt
    assert "Your intuition is allowed only as a suspicion signal" in guardrail_prompt
    assert "required_repairs" in guardrail_prompt


def test_graph_cot_contracts_serialize_nested_payloads() -> None:
    question_frame = GraphCoTQuestionFrame(
        question="Who did ACME acquire?",
        workspace_id="ws-1",
        databases=("news_kg",),
        intent_id="relationship_lookup",
        entity_candidates=("ACME",),
    )
    evidence = QueryEvidencePacket(
        database="news_kg",
        cypher="MATCH (a)-[:ACQUIRED]->(b) RETURN a, b",
        records=({"source_entity": "ACME", "target_entity": "Beta"},),
        selected_triples=(
            {"source_entity": "ACME", "relation_type": "ACQUIRED", "target_entity": "Beta"},
        ),
        slot_fills={"target_entity": "Beta"},
        grounded_slots=("target_entity",),
        support_status="supported",
        ontology_context_mismatch={"status": "match"},
    )
    draft = AnswerDraft(
        answer_text="ACME acquired Beta.",
        cited_facts=("ACME ACQUIRED Beta",),
        grounded_slots=("target_entity",),
    )
    verdict = GuardrailVerdict(
        decision="pass",
        summary="All answer claims are supported.",
        supported_claims=("ACME acquired Beta.",),
        hard_findings=(
            GuardrailFinding(
                code="none",
                severity="soft",
                message="No hard finding.",
            ),
        ),
    )
    final_answer = GraphCoTFinalAnswer(
        answer_text="ACME acquired Beta.",
        status="answered",
        draft=draft,
        verdict=verdict,
        evidence=evidence,
    )

    assert question_frame.query_mode == "graph_cot"
    assert evidence.has_grounded_support is True
    assert draft.is_partial is False
    assert verdict.allows_answer is True
    assert final_answer.to_dict()["verdict"]["decision"] == "pass"
    assert final_answer.to_dict()["evidence"]["slot_fills"]["target_entity"] == "Beta"
