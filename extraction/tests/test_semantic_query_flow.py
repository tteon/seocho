import json
import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from semantic_query_flow import QueryRouterAgent, SemanticAgentFlow, SemanticEntityResolver


class FakeConnector:
    def run_cypher(self, query, database="neo4j", params=None):
        params = params or {}

        if "SHOW FULLTEXT INDEXES" in query:
            return json.dumps([{"name": "entity_fulltext"}])

        if "SHOW INDEXES" in query:
            return json.dumps([{"name": "entity_fulltext"}])

        if "CALL db.index.fulltext.queryNodes" in query:
            text = str(params.get("query", "")).lower()
            if text == "neo4j":
                return json.dumps(
                    [
                        {
                            "node_id": 101,
                            "labels": ["Database"],
                            "display_name": "Neo4j",
                            "score": 3.2,
                        }
                    ]
                )
            return json.dumps([])

        if "WHERE any(key IN $properties" in query:
            return json.dumps(
                [
                    {
                        "node_id": 202,
                        "labels": ["Concept"],
                        "display_name": "GraphRAG",
                    }
                ]
            )

        if "WHERE elementId(n) = toString($node_id)" in query:
            return json.dumps(
                [
                    {
                        "entity": "Neo4j",
                        "labels": ["Database"],
                        "neighbors": [
                            {"type": "USES", "target": "Cypher", "target_labels": ["Language"]}
                        ],
                    }
                ]
            )

        if "toLower(lbl) IN ['resource', 'class', 'ontology', 'individual']" in query:
            return json.dumps([])

        if "RETURN labels(n)[0] AS label, count(*) AS count" in query:
            return json.dumps([{"label": "Database", "count": 1}])

        return json.dumps([])


def test_extract_question_entities():
    resolver = SemanticEntityResolver(FakeConnector())
    entities = resolver.extract_question_entities('What is "Neo4j" relation to GraphRAG?')
    assert "Neo4j" in entities
    assert "GraphRAG" in entities


def test_resolve_entities_prefers_fulltext_then_fallback():
    resolver = SemanticEntityResolver(FakeConnector())
    result = resolver.resolve("Tell me about Neo4j and GraphRAG", ["kgnormal"])

    assert "Neo4j" in result["matches"]
    assert result["matches"]["Neo4j"][0]["source"] == "fulltext"
    assert "GraphRAG" in result["matches"]
    assert result["matches"]["GraphRAG"][0]["source"] == "contains"


def test_router_rdf_detection():
    router = QueryRouterAgent()
    assert router.route("Show RDF ontology class for Person") == "rdf"
    assert router.route("Find graph neighbors for Neo4j node") == "lpg"


def test_semantic_agent_flow_lpg_path():
    flow = SemanticAgentFlow(FakeConnector())
    result = flow.run("What is Neo4j connected to?", ["kgnormal"])

    assert result["route"] == "lpg"
    assert result["semantic_context"]["entities"]
    assert result["lpg_result"] is not None
    assert result["lpg_result"]["records"]
    assert "Route selected: LPG." in result["response"]


def test_resolve_applies_ontology_alias_hint(tmp_path, monkeypatch):
    hints_path = tmp_path / "ontology_hints.json"
    hints_path.write_text(
        json.dumps(
            {
                "aliases": {"neo4-j": "Neo4j"},
                "label_keywords": {"database": ["database", "db"]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ONTOLOGY_HINTS_PATH", str(hints_path))
    resolver = SemanticEntityResolver(FakeConnector())
    result = resolver.resolve("Tell me about Neo4-j database", ["kgnormal"])

    assert result["alias_resolved"]["Neo4-j"] == "Neo4j"
    assert "database" in result["label_hints"]


def test_semantic_agent_flow_applies_entity_overrides():
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
    assert result["semantic_context"]["matches"]["Neo4j"][0]["source"] == "override"
