import json

from seocho.query.semantic_agents import (
    AnswerGenerationAgent,
    QueryRouterAgent,
    SemanticEntityResolver,
)


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
        if "any(key IN $properties" in query or "n.name IS NOT NULL" in query:
            text = str(params.get("query", "")).lower()
            if "nvidia" in text:
                return json.dumps(
                    [
                        {
                            "node_id": 303,
                            "labels": ["Company"],
                            "display_name": "NVIDIA Corporation",
                            "source_id": "mem_nvidia",
                            "memory_id": "mem_nvidia",
                        }
                    ]
                )
            return json.dumps([])
        return json.dumps([])


def test_canonical_query_router_routes_rdf_and_lpg():
    router = QueryRouterAgent()
    assert router.route("Show RDF ontology class for Person") == "rdf"
    assert router.route("Find graph neighbors for Neo4j node") == "lpg"


def test_canonical_semantic_entity_resolver_uses_hint_files(tmp_path, monkeypatch):
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
    assert result["matches"]["Neo4-j"][0]["display_name"] == "Neo4j"


def test_canonical_semantic_entity_resolver_strips_leading_wrapper_tokens():
    resolver = SemanticEntityResolver(FakeConnector())

    entities = resolver.extract_question_entities(
        "For Tesla Inc., how many vehicles were delivered in 2022 compared with 2021?"
    )

    assert "Tesla Inc" in entities
    assert "For Tesla Inc" not in entities


def test_canonical_semantic_entity_resolver_uses_explicit_property_contains_lookup():
    resolver = SemanticEntityResolver(FakeConnector())

    result = resolver.resolve("What drove NVIDIA's gross margin expansion?", ["kgnormal"])

    assert result["matches"]["NVIDIA"][0]["source"] == "contains"
    assert result["matches"]["NVIDIA"][0]["display_name"] == "NVIDIA Corporation"
    assert result["unresolved_entities"] == []


def test_canonical_answer_generation_agent_synthesizes_support_summary():
    agent = AnswerGenerationAgent()
    response = agent.synthesize(
        question="What is Neo4j connected to?",
        route="lpg",
        semantic_context={
            "entities": ["Neo4j"],
            "intent": {"intent_id": "relationship_lookup"},
            "support_assessment": {"status": "supported", "reason": "grounded"},
            "evidence_bundle_preview": {
                "grounded_slots": ["target_entity", "relation_paths"],
                "missing_slots": ["source_entity"],
            },
            "strategy_decision": {"next_mode_hint": "reasoning_mode"},
            "reasoning": {"requested": False},
        },
        lpg_result={"records": [{"target_entity": "Cypher"}]},
        rdf_result=None,
    )

    assert "Route selected: LPG." in response
    assert "Support status: supported (grounded)." in response
    assert "LPG records: 1." in response
