import json

from seocho.query.semantic_flow import SemanticAgentFlow
from seocho.query.semantic_agents import AnswerGenerationAgent


class FakeConnector:
    def run_cypher(self, query, database="neo4j", params=None):
        params = params or {}

        if "SHOW FULLTEXT INDEXES" in query or "SHOW INDEXES" in query:
            return json.dumps([{"name": "entity_fulltext"}])

        if "CALL db.index.fulltext.queryNodes" in query:
            text = str(params.get("query", "")).lower()
            if "neo4j" in text:
                return json.dumps(
                    [
                        {
                            "node_id": 101,
                            "labels": ["Database"],
                            "display_name": "Neo4j",
                            "source_id": "mem_neo4j",
                            "memory_id": "mem_neo4j",
                            "score": 3.2,
                        }
                    ]
                )
            return json.dumps([])

        if "AS source_entity" in query and "AS relation_type" in query:
            return json.dumps(
                [
                    {
                        "source_entity": "Neo4j",
                        "relation_type": "USES",
                        "target_entity": "Cypher",
                        "target_labels": ["Language"],
                        "supporting_fact": "Neo4j uses Cypher.",
                    }
                ]
            )

        if "RETURN labels(n)[0] AS label, count(*) AS count" in query:
            return json.dumps([{"label": "Database", "count": 1}])

        if "toLower(lbl) IN ['resource', 'class', 'ontology', 'individual']" in query:
            return json.dumps([])

        if "any(key IN $properties" in query:
            return json.dumps([])

        return json.dumps([])


class FailingRelationshipConnector(FakeConnector):
    def run_cypher(self, query, database="neo4j", params=None):
        if "AS source_entity" in query and "AS relation_type" in query:
            return "Error executing Cypher in 'neo4j': simulated contract failure"
        return super().run_cypher(query, database=database, params=params)


class SwarmExpansionConnector(FakeConnector):
    def run_cypher(self, query, database="neo4j", params=None):
        params = params or {}
        if "AS source_entity" in query and "AS relation_type" in query:
            if str(params.get("target_hint") or "").strip() or "MATCH (n:Database)" in query:
                return json.dumps([])
            return json.dumps(
                [
                    {
                        "source_entity": "Neo4j",
                        "relation_type": "POWERS",
                        "target_entity": "GraphRAG",
                        "target_labels": ["Technique"],
                        "supporting_fact": "Neo4j powers GraphRAG relation retrieval.",
                    }
                ]
            )
        return super().run_cypher(query, database=database, params=params)


class EntitySummaryConnector(FakeConnector):
    def run_cypher(self, query, database="neo4j", params=None):
        params = params or {}

        if "CALL db.index.fulltext.queryNodes" in query:
            text = str(params.get("query", "")).lower()
            if "amazon" in text:
                return json.dumps(
                    [
                        {
                            "node_id": 202,
                            "labels": ["Company"],
                            "display_name": "Amazon",
                            "source_id": "mem_amazon",
                            "memory_id": "mem_amazon",
                            "score": 3.4,
                        }
                    ]
                )
            return json.dumps([])

        if "properties(n) AS properties" in query and "AS neighbors" in query:
            return json.dumps(
                [
                    {
                        "target_entity": "Amazon",
                        "properties": {"name": "Amazon"},
                        "neighbors": [
                            {
                                "relation": "FACES",
                                "target": "dependence on third-party sellers",
                                "target_labels": ["Risk"],
                            },
                            {
                                "relation": "FACES",
                                "target": "regulatory challenges in multiple jurisdictions",
                                "target_labels": ["Risk"],
                            },
                            {
                                "relation": "FACES",
                                "target": "cybersecurity threats to customer data",
                                "target_labels": ["Risk"],
                            },
                            {
                                "relation": "FACES",
                                "target": "significant capital expenditure requirements for AWS infrastructure",
                                "target_labels": ["Risk"],
                            },
                            {
                                "relation": "FACES",
                                "target": "competition in e-commerce and cloud computing markets",
                                "target_labels": ["Risk"],
                            },
                        ],
                        "supporting_fact": "",
                    }
                ]
            )

        return super().run_cypher(query, database=database, params=params)


class EngineeringTradeoffConnector(FakeConnector):
    def run_cypher(self, query, database="neo4j", params=None):
        params = params or {}

        if "CALL db.index.fulltext.queryNodes" in query:
            text = str(params.get("query", "")).lower()
            if "python" in text:
                return json.dumps(
                    [
                        {
                            "node_id": 303,
                            "labels": ["Language"],
                            "display_name": "Python",
                            "source_id": "mem_python",
                            "memory_id": "mem_python",
                            "score": 3.6,
                        }
                    ]
                )
            return json.dumps([])

        if "properties(n) AS properties" in query and "AS neighbors" in query:
            return json.dumps(
                [
                    {
                        "target_entity": "Python",
                        "properties": {"name": "Python"},
                        "neighbors": [
                            {
                                "relation": "LIMITED_BY",
                                "target": "GIL",
                                "target_labels": ["Limitation"],
                            },
                            {
                                "relation": "PARALLELIZED_WITH",
                                "target": "multiprocessing",
                                "target_labels": ["Alternative"],
                            },
                            {
                                "relation": "PARALLELIZED_WITH",
                                "target": "Ray",
                                "target_labels": ["Alternative"],
                            },
                        ],
                        "supporting_fact": "",
                    }
                ]
            )

        return super().run_cypher(query, database=database, params=params)


def test_canonical_semantic_agent_flow_runs_end_to_end():
    flow = SemanticAgentFlow(FakeConnector())
    result = flow.run("What is Neo4j connected to?", ["kgnormal"])

    assert result["route"] == "lpg"
    assert result["support_assessment"]["status"] == "supported"
    assert result["strategy_decision"]["executed_mode"] == "semantic_direct"
    assert result["evidence_bundle"]["slot_fills"]["relation_paths"] == ["USES"]
    assert result["latency_breakdown_ms"]["retrieval_ms"] >= 0
    assert result["latency_breakdown_ms"]["generation_ms"] >= 0
    assert result["agent_pattern"]["schema_version"] == "agent_pattern_receipt.v1"
    assert result["agent_pattern"]["pattern"] == "semantic_direct"
    assert result["answer_envelope"]["schema_version"] == "answer_envelope.v1"
    assert result["answer_envelope"]["evidence_bundle"]["slot_fills"]["relation_paths"] == ["USES"]
    assert result["response"].startswith("Neo4j uses Cypher.")
    assert "Route selected: LPG." in result["response"]


def test_canonical_semantic_agent_flow_applies_entity_overrides():
    flow = SemanticAgentFlow(FakeConnector())
    result = flow.run(
        question="What is Neo4j connected to?",
        databases=["kgnormal"],
        entity_overrides={
            "Neo4j": {
                "database": "kgnormal",
                "node_id": 777,
                "display_name": "Neo4j Override",
                "labels": ["Database"],
            }
        },
    )

    applied = result["semantic_context"].get("overrides_applied", {})
    assert "Neo4j" in applied
    assert applied["Neo4j"]["node_id"] == 777


def test_canonical_semantic_agent_flow_supports_graph_cot_query_mode():
    flow = SemanticAgentFlow(FakeConnector())
    result = flow.run(
        "What is Neo4j connected to?",
        ["kgnormal"],
        query_mode="graph_cot",
    )

    assert result["query_mode"] == "graph_cot"
    assert result["strategy_decision"]["requested_mode"] == "graph_cot"
    assert result["strategy_decision"]["executed_mode"] == "graph_cot_repair"
    assert result["semantic_context"]["query_mode"] == "graph_cot"
    assert result["agent_pattern"]["pattern"] == "graph_cot"
    assert result["answer_envelope"]["query_mode"] == "graph_cot"
    assert result["graph_cot"]["supervisor_directive"]["route"] == "lpg"
    assert result["graph_cot"]["guardrail_verdict"]["decision"] == "pass"
    assert any(step["agent"] == "QuerySupervisorAgent" for step in result["trace_steps"])
    assert any(step["agent"] == "Text2CypherAgent" for step in result["trace_steps"])
    assert any(step["agent"] == "AnswerGuardrailAgent" for step in result["trace_steps"])


def test_answer_generation_preserves_long_supporting_sentence_product_ids():
    supporting_fact = (
        "NVIDIA Corporation reported data center revenue of $15.0 billion in fiscal 2024, "
        "up 217% from $4.7 billion in fiscal 2023. Gaming revenue was $10.4 billion, up 15%. "
        "The company's gross margin expanded to 72.7% from 56.9%, driven by strong demand "
        "for AI accelerator chips including the H100 and A100 product lines."
    )
    response = AnswerGenerationAgent().synthesize(
        question="What drove NVIDIA's gross margin expansion?",
        route="lpg",
        semantic_context={
            "entities": ["NVIDIA"],
            "intent": {"intent_id": "entity_summary"},
            "evidence_bundle_preview": {
                "slot_fills": {"supporting_fact": supporting_fact},
                "grounded_slots": ["target_entity", "supporting_fact"],
            },
            "support_assessment": {"status": "supported", "reason": "sufficient"},
            "strategy_decision": {},
        },
        lpg_result={"records": [{"entity": "NVIDIA"}]},
        rdf_result=None,
    )

    direct_answer = response.split("Route selected:", 1)[0]
    assert "H100 and A100 product lines" in direct_answer


def test_canonical_semantic_flow_reports_query_contract_failures():
    flow = SemanticAgentFlow(FailingRelationshipConnector())

    result = flow.run(
        "What is Neo4j connected to?",
        ["kgnormal"],
        reasoning_mode=True,
        repair_budget=1,
    )

    assert result["query_diagnostics"]
    assert result["query_diagnostics"][0]["diagnosis_code"] == "query_execution_failed_or_contract_error"
    assert result["lpg_result"]["reasoning"]["query_failure_count"] >= 1


def test_semantic_repair_budget_runs_swarm_relation_expansion_without_graph_cot():
    flow = SemanticAgentFlow(SwarmExpansionConnector())

    result = flow.run(
        "What is Neo4j related to GraphRAG?",
        ["kgnormal"],
        repair_budget=1,
    )

    repair_trace = result["lpg_result"]["reasoning"]["repair_trace"]
    swarm_steps = [step for step in repair_trace if step.get("swarm_action")]
    assert result["query_mode"] == "semantic"
    assert result["strategy_decision"]["executed_mode"] == "semantic_repair"
    assert result["support_assessment"]["status"] == "supported"
    assert result["agent_pattern"]["pattern"] == "reflection_chain"
    assert swarm_steps
    assert swarm_steps[0]["swarm_action"] == "relation_path_expansion"
    assert result["evidence_bundle"]["slot_fills"]["relation_paths"] == ["POWERS"]
    assert result["evidence_bundle"]["provenance"]
    assert result["evidence_bundle"]["evidence_swarm"]["recommended_next_step"] in {
        "direct_answer",
        "slot_bundle_then_synthesis",
    }


def test_canonical_semantic_flow_surfaces_reasoning_cycle_for_unsupported_support():
    class UnsupportedConnector(FakeConnector):
        def run_cypher(self, query, database="neo4j", params=None):
            if "AS source_entity" in query and "AS relation_type" in query:
                return json.dumps([])
            return super().run_cypher(query, database=database, params=params)

    flow = SemanticAgentFlow(UnsupportedConnector())

    result = flow.run(
        "What is Neo4j related to GraphRAG?",
        ["kgnormal"],
        reasoning_cycle={
            "enabled": True,
            "anomaly_sources": ["unsupported_answer", "query_diagnostic"],
            "abduction": {"mode": "candidate_only"},
            "deduction": {"require_testable_predictions": True},
            "induction": {"require_support_assessment": True},
            "promotion": {"analyst_approval_required": True},
        },
    )

    assert result["support_assessment"]["status"] == "partial"
    assert result["reasoning_cycle"]["status"] == "anomaly_detected"
    assert result["reasoning_cycle"]["observed_anomalies"][0]["source"] == "unsupported_answer"
    assert result["semantic_context"]["reasoning_cycle"]["next_phase"] == "abduction"


def test_canonical_semantic_flow_synthesizes_risk_summary_from_neighbors():
    flow = SemanticAgentFlow(EntitySummaryConnector())

    result = flow.run("What are Amazon's key risk factors?", ["kgnormal"])

    assert result["route"] == "lpg"
    assert result["support_assessment"]["status"] == "supported"
    assert "Amazon's key risks include dependence on third-party sellers" in result["response"]
    assert "cybersecurity threats to customer data" in result["response"]
    assert (
        result["evidence_bundle"]["slot_fills"]["supporting_fact"]
        == "Amazon's key risks include dependence on third-party sellers, "
        "regulatory challenges in multiple jurisdictions, cybersecurity threats to customer data, "
        "significant capital expenditure requirements for AWS infrastructure, "
        "competition in e-commerce and cloud computing markets."
    )


def test_canonical_semantic_flow_synthesizes_engineering_tradeoff_answer():
    flow = SemanticAgentFlow(EngineeringTradeoffConnector())

    result = flow.run("What limits Python parallel work, and what alternatives avoid the GIL?", ["kgnormal"])

    assert result["route"] == "lpg"
    assert result["semantic_context"]["intent"]["intent_id"] == "engineering_tradeoff_lookup"
    assert result["support_assessment"]["status"] == "supported"
    assert result["response"].startswith("Python is limited by GIL.")
    assert "Alternatives for Python parallel work include multiprocessing, Ray." in result["response"]
    assert result["evidence_bundle"]["slot_fills"]["limitation_points"] == ["GIL"]
    assert result["evidence_bundle"]["slot_fills"]["alternative_points"] == ["multiprocessing", "Ray"]


def test_graph_cot_guardrail_revises_unsupported_answer_to_abstention():
    class UnsupportedConnector(FakeConnector):
        def run_cypher(self, query, database="neo4j", params=None):
            if "AS source_entity" in query and "AS relation_type" in query:
                return json.dumps([])
            return super().run_cypher(query, database=database, params=params)

    flow = SemanticAgentFlow(UnsupportedConnector())

    result = flow.run(
        "What is Neo4j related to GraphRAG?",
        ["kgnormal"],
        query_mode="graph_cot",
    )

    assert result["graph_cot"]["revision_count"] == 1
    assert result["graph_cot"]["review_history"][0]["decision"] == "revise"
    assert result["graph_cot"]["final_answer"]["status"] == "abstained"
    assert "I could not verify a grounded answer from the current graph evidence." in result["response"]


def test_graph_cot_guardrail_adds_ontology_drift_caveat():
    flow = SemanticAgentFlow(FakeConnector())

    result = flow.run(
        "What is Neo4j connected to?",
        ["kgnormal"],
        query_mode="graph_cot",
        ontology_context_mismatch={
            "mismatch": True,
            "warning": "active profile differs from indexed graph context",
        },
    )

    assert result["graph_cot"]["revision_count"] == 1
    assert result["graph_cot"]["review_history"][0]["decision"] == "revise"
    assert "Ontology context warning:" in result["response"]
