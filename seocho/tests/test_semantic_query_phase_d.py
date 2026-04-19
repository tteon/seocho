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


def test_canonical_semantic_agent_flow_runs_end_to_end():
    flow = SemanticAgentFlow(FakeConnector())
    result = flow.run("What is Neo4j connected to?", ["kgnormal"])

    assert result["route"] == "lpg"
    assert result["support_assessment"]["status"] == "supported"
    assert result["strategy_decision"]["executed_mode"] == "semantic_direct"
    assert result["evidence_bundle"]["slot_fills"]["relation_paths"] == ["USES"]
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
