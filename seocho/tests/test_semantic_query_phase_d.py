import json

from seocho.query.semantic_flow import SemanticAgentFlow


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


def test_canonical_semantic_agent_flow_runs_end_to_end():
    flow = SemanticAgentFlow(FakeConnector())
    result = flow.run("What is Neo4j connected to?", ["kgnormal"])

    assert result["route"] == "lpg"
    assert result["support_assessment"]["status"] == "supported"
    assert result["strategy_decision"]["executed_mode"] == "semantic_direct"
    assert result["evidence_bundle"]["slot_fills"]["relation_paths"] == ["USES"]
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
