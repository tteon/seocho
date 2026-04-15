import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from runtime.memory_service import GraphMemoryService


class _FakeIngestor:
    def __init__(self):
        self.calls = []

    def ingest_records(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "records_processed": 1,
            "records_failed": 0,
            "total_nodes": 3,
            "total_relationships": 2,
            "warnings": [],
            "semantic_artifacts": {"ontology_candidate": {}, "shacl_candidate": {}, "vocabulary_candidate": {}},
        }


class _FakeResolver:
    def resolve(self, question, databases, workspace_id="default"):
        return {"entities": ["Seoul"], "matches": {}, "unresolved_entities": []}


class _FakeSemanticFlow:
    def __init__(self):
        self.resolver = _FakeResolver()


class _FakeDbManager:
    driver = None


def test_create_memory_calls_runtime_ingest_with_workspace_and_scope():
    ingestor = _FakeIngestor()
    service = GraphMemoryService(
        db_manager=_FakeDbManager(),
        runtime_raw_ingestor=ingestor,
        semantic_agent_flow=_FakeSemanticFlow(),
    )

    payload = service.create_memory(
        workspace_id="default",
        content="Alice manages Seoul retail.",
        metadata={"source": "note"},
        user_id="user_1",
        agent_id="agent_1",
        session_id="sess_1",
    )

    assert payload["memory"]["workspace_id"] == "default"
    assert payload["memory"]["status"] == "stored"
    assert ingestor.calls[0]["workspace_id"] == "default"
    assert ingestor.calls[0]["records"][0]["metadata"]["user_id"] == "user_1"
    assert ingestor.calls[0]["records"][0]["metadata"]["agent_id"] == "agent_1"
    assert ingestor.calls[0]["records"][0]["metadata"]["session_id"] == "sess_1"


def test_chat_from_memories_uses_search_results_for_response():
    ingestor = _FakeIngestor()
    service = GraphMemoryService(
        db_manager=_FakeDbManager(),
        runtime_raw_ingestor=ingestor,
        semantic_agent_flow=_FakeSemanticFlow(),
    )

    service.search_memories = lambda **_: {
        "results": [
            {
                "memory_id": "mem_1",
                "content": "Alice manages Seoul retail.",
                "content_preview": "Alice manages Seoul retail.",
                "metadata": {"source": "note"},
                "score": 0.93,
                "reasons": ["entity_match"],
                "matched_entities": ["Seoul"],
                "database": "kgnormal",
                "status": "active",
            }
        ],
        "semantic_context": {"entities": ["Seoul"], "matches": {}, "unresolved_entities": []},
    }

    payload = service.chat_from_memories(workspace_id="default", message="Who manages Seoul retail?")

    assert payload["assistant_message"] == "Alice manages Seoul retail."
    assert payload["memory_hits"][0]["memory_id"] == "mem_1"
    assert payload["evidence_bundle"]["intent_id"] == "responsibility_lookup"


def test_search_memories_adds_evidence_bundle_to_ranked_results():
    class _ResolverWithMatches:
        def resolve(self, question, databases, workspace_id="default"):
            return {
                "entities": ["Seoul"],
                "matches": {
                    "Seoul": [
                        {
                            "database": "kgnormal",
                            "memory_id": "mem_1",
                            "source_id": "mem_1",
                            "display_name": "Seoul Retail",
                            "node_id": "101",
                            "labels": ["Account"],
                            "source": "fulltext",
                            "final_score": 0.93,
                        }
                    ]
                },
                "unresolved_entities": [],
                "intent": {
                    "intent_id": "responsibility_lookup",
                    "required_relations": ["MANAGES"],
                    "required_entity_types": ["Person", "Organization"],
                    "focus_slots": ["owner_or_operator", "target_entity", "supporting_fact"],
                },
                "evidence_bundle_preview": {"intent_id": "responsibility_lookup"},
            }

    class _SemanticFlowWithMatches:
        def __init__(self):
            self.resolver = _ResolverWithMatches()

    ingestor = _FakeIngestor()
    service = GraphMemoryService(
        db_manager=_FakeDbManager(),
        runtime_raw_ingestor=ingestor,
        semantic_agent_flow=_SemanticFlowWithMatches(),
    )

    service.get_memory = lambda **_: {
        "memory_id": "mem_1",
        "workspace_id": "default",
        "content": "Alice manages Seoul retail.",
        "content_preview": "Alice manages Seoul retail.",
        "metadata": {"source": "note"},
        "status": "active",
        "created_at": "2026-03-13T00:00:00Z",
        "updated_at": "2026-03-13T00:00:00Z",
        "database": "kgnormal",
        "entities": [
            {"id": "n1", "labels": ["Person"], "name": "Alice"},
            {"id": "n2", "labels": ["Account"], "name": "Seoul Retail"},
        ],
    }

    payload = service.search_memories(
        workspace_id="default",
        query="Who manages Seoul retail?",
        limit=3,
    )

    result = payload["results"][0]
    assert result["evidence_bundle"]["intent_id"] == "responsibility_lookup"
    assert result["evidence_bundle"]["slot_fills"]["owner_or_operator"] == "Alice"
    assert result["evidence_bundle"]["slot_fills"]["target_entity"] == "Seoul"
