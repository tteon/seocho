import json
import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from semantic_query_flow import QueryRouterAgent, SemanticAgentFlow, SemanticEntityResolver
from semantic_artifact_store import approve_semantic_artifact, save_semantic_artifact
from semantic_run_store import get_semantic_run, list_semantic_runs


class FakeConnector:
    def run_cypher(self, query, database="neo4j", params=None):
        params = params or {}

        if "SHOW FULLTEXT INDEXES" in query:
            return json.dumps([{"name": "entity_fulltext"}])

        if "SHOW INDEXES" in query:
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

        if "any(key IN $properties" in query:
            return json.dumps(
                [
                    {
                        "node_id": 202,
                        "labels": ["Concept"],
                        "display_name": "GraphRAG",
                        "source_id": "mem_graphrag",
                        "memory_id": "mem_graphrag",
                    }
                ]
            )

        if "AS source_entity" in query and "AS relation_type" in query:
            if params.get("target_hint"):
                return json.dumps([])
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

        if "AS owner_or_operator" in query:
            return json.dumps(
                [
                    {
                        "owner_or_operator": "Alex",
                        "relation_type": "MANAGES",
                        "target_entity": "Seoul Retail",
                        "owner_labels": ["Person"],
                        "target_labels": ["Account"],
                        "supporting_fact": "Alex manages Seoul Retail.",
                    }
                ]
            )

        if "properties(n) AS properties" in query:
            return json.dumps(
                [
                    {
                        "target_entity": "Neo4j",
                        "properties": {"name": "Neo4j", "category": "Database"},
                        "neighbors": [
                            {"relation": "USES", "target": "Cypher", "target_labels": ["Language"]}
                        ],
                        "supporting_fact": "Neo4j is a graph database.",
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
    result = resolver.resolve('Tell me about Neo4j and "GraphRAG"', ["kgnormal"])

    assert result["intent"]["intent_id"] == "entity_summary"
    assert "Neo4j" in result["matches"]
    assert result["matches"]["Neo4j"][0]["source"] == "fulltext"
    assert result["matches"]["Neo4j"][0]["memory_id"] == "mem_neo4j"
    assert "GraphRAG" in result["matches"]
    assert result["matches"]["GraphRAG"][0]["source"] == "contains"
    assert result["matches"]["GraphRAG"][0]["memory_id"] == "mem_graphrag"
    assert result["evidence_bundle_preview"]["intent_id"] == "entity_summary"
    assert result["evidence_bundle_preview"]["candidate_entities"]


def test_router_rdf_detection():
    router = QueryRouterAgent()
    assert router.route("Show RDF ontology class for Person") == "rdf"
    assert router.route("Find graph neighbors for Neo4j node") == "lpg"


def test_semantic_agent_flow_lpg_path():
    flow = SemanticAgentFlow(FakeConnector())
    result = flow.run("What is Neo4j connected to?", ["kgnormal"])

    assert result["route"] == "lpg"
    assert result["semantic_context"]["entities"]
    assert result["semantic_context"]["intent"]["intent_id"] == "relationship_lookup"
    assert result["semantic_context"]["evidence_bundle_preview"]["slot_fills"]["relation_paths"] == ["USES"]
    assert result["lpg_result"] is not None
    assert result["lpg_result"]["records"]
    assert "Route selected: LPG." in result["response"]
    assert "Intent: relationship_lookup." in result["response"]


def test_semantic_agent_flow_uses_canonical_phase_a_query_primitives():
    flow = SemanticAgentFlow(FakeConnector())

    assert flow.lpg_agent.validator.__class__.__module__ == "seocho.query.cypher_validator"
    assert flow.lpg_agent.classifier.__class__.__module__ == "seocho.query.insufficiency"
    assert flow.lpg_agent.support_validator.__class__.__module__ == "seocho.query.strategy_chooser"
    assert flow.strategy_chooser.__class__.__module__ == "seocho.query.strategy_chooser"


def test_semantic_agent_flow_uses_canonical_phase_b_support_classes():
    flow = SemanticAgentFlow(FakeConnector())

    assert flow.lpg_agent.constraint_builder.__class__.__module__ == "seocho.query.constraints"
    assert flow.constraint_builder.__class__.__module__ == "seocho.query.constraints"
    assert flow.run_registry.__class__.__module__ == "seocho.query.run_registry"


def test_semantic_agent_flow_reasoning_mode_repairs_initially_insufficient_query():
    flow = SemanticAgentFlow(FakeConnector())
    result = flow.run(
        "What is Neo4j related to GraphRAG?",
        ["kgnormal"],
        reasoning_mode=True,
        repair_budget=2,
    )

    reasoning = result["semantic_context"]["reasoning"]
    assert reasoning["requested"] is True
    assert reasoning["attempt_count"] >= 2
    assert reasoning["terminal_reason"] == "sufficient"
    assert result["lpg_result"]["records"]


def test_semantic_agent_flow_emits_support_strategy_and_run_metadata(tmp_path, monkeypatch):
    registry_path = tmp_path / "semantic_runs.db"
    monkeypatch.setenv("SEOCHO_SEMANTIC_METADATA_DB", str(registry_path))
    flow = SemanticAgentFlow(FakeConnector())

    result = flow.run("What is Neo4j connected to?", ["kgnormal"])

    assert result["support_assessment"]["status"] == "supported"
    assert result["strategy_decision"]["executed_mode"] == "semantic_direct"
    assert result["evidence_bundle"]["schema_version"] == "evidence_bundle.v2"
    assert result["evidence_bundle"]["grounded_slots"]
    assert result["run_metadata"]["recorded"] is True
    assert registry_path.exists()

    rows = list_semantic_runs("default", base_dir=str(registry_path))
    assert len(rows) == 1
    record = get_semantic_run("default", result["run_metadata"]["run_id"], base_dir=str(registry_path))
    assert rows[0]["run_id"] == result["run_metadata"]["run_id"]
    assert record["route"] == "lpg"
    assert record["intent_id"] == "relationship_lookup"


def test_semantic_agent_flow_recommends_advanced_for_multi_graph_insufficient_result():
    flow = SemanticAgentFlow(FakeConnector())

    result = flow.run("What is Neo4j related to GraphRAG?", ["kgnormal", "kgfibo"])

    assert result["lpg_result"]["summary"].startswith("No grounded LPG result satisfied")
    assert result["strategy_decision"]["advanced_debate_recommended"] is True
    assert result["strategy_decision"]["next_mode_hint"] == "advanced"


def test_semantic_agent_flow_recommends_reasoning_mode_before_advanced_on_single_graph():
    flow = SemanticAgentFlow(FakeConnector())

    result = flow.run("What is Neo4j related to GraphRAG?", ["kgnormal"])

    assert result["support_assessment"]["supported"] is False
    assert result["strategy_decision"]["next_mode_hint"] == "reasoning_mode"


def test_semantic_agent_flow_emits_deterministic_profile_metadata():
    flow = SemanticAgentFlow(FakeConnector())

    result = flow.run("Who manages Seoul retail?", ["kgnormal"])

    assert result["evidence_bundle"]["deterministic_profile"]["profile_id"] == "baseline.responsibility.v1"
    assert result["evidence_bundle"]["reasoning"]["profile_id"] == "baseline.responsibility.v1"


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


def test_resolve_prefers_workspace_vocabulary_over_global(tmp_path, monkeypatch):
    base_dir = str(tmp_path)
    monkeypatch.setenv("SEMANTIC_ARTIFACT_DIR", base_dir)
    monkeypatch.setenv("VOCABULARY_GLOBAL_WORKSPACE_ID", "global")
    monkeypatch.setenv("ONTOLOGY_HINTS_PATH", str(tmp_path / "missing.json"))

    global_artifact = save_semantic_artifact(
        workspace_id="global",
        name="global_vocab",
        ontology_candidate={
            "ontology_name": "global",
            "classes": [{"name": "DozerDB", "aliases": ["Neo4j"]}],
            "relationships": [],
        },
        shacl_candidate={"shapes": []},
        base_dir=base_dir,
    )
    approve_semantic_artifact(
        workspace_id="global",
        artifact_id=global_artifact["artifact_id"],
        approved_by="reviewer",
        base_dir=base_dir,
    )

    workspace_artifact = save_semantic_artifact(
        workspace_id="default",
        name="workspace_vocab",
        ontology_candidate={
            "ontology_name": "workspace",
            "classes": [{"name": "Neo4j Enterprise", "aliases": ["Neo4j"]}],
            "relationships": [],
        },
        shacl_candidate={"shapes": []},
        base_dir=base_dir,
    )
    approve_semantic_artifact(
        workspace_id="default",
        artifact_id=workspace_artifact["artifact_id"],
        approved_by="reviewer",
        base_dir=base_dir,
    )

    resolver = SemanticEntityResolver(FakeConnector())
    result = resolver.resolve("Tell me about Neo4j", ["kgnormal"], workspace_id="default")

    assert result["vocabulary_resolved"]["Neo4j"] == "Neo4j Enterprise"
    assert result["alias_resolved"]["Neo4j"] == "Neo4j Enterprise"
    assert result["vocabulary_hints"]["approved_artifact_counts"]["global"] == 1
    assert result["vocabulary_hints"]["approved_artifact_counts"]["workspace"] == 1
