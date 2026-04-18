import os
import sys
from unittest.mock import patch


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import runtime.memory_service as memory_service_mod
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


class _RecordingQueryProxy:
    def __init__(self) -> None:
        self.calls = []

    def query(self, request):
        self.calls.append(request)
        return [{"memory_id": "mem_1"}]


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
                "evidence_bundle": {
                    "intent_id": "responsibility_lookup",
                    "slot_fills": {"owner_or_operator": "Alice", "target_entity": "Seoul Retail"},
                },
            }
        ],
        "semantic_context": {"entities": ["Seoul"], "matches": {}, "unresolved_entities": []},
        "ontology_context_mismatch": {"mismatch": False, "databases": []},
    }

    payload = service.chat_from_memories(workspace_id="default", message="Who manages Seoul retail?")

    assert payload["assistant_message"] == "Alice manages Seoul retail."
    assert payload["memory_hits"][0]["memory_id"] == "mem_1"
    assert payload["evidence_bundle"]["intent_id"] == "responsibility_lookup"
    assert payload["evidence_bundle"]["slot_fills"]["owner_or_operator"] == "Alice"
    assert payload["ontology_context_mismatch"]["mismatch"] is False


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


def test_scope_filter_allows_workspace_level_memory_for_scoped_query():
    service = GraphMemoryService(
        db_manager=_FakeDbManager(),
        runtime_raw_ingestor=_FakeIngestor(),
        semantic_agent_flow=_FakeSemanticFlow(),
    )

    assert service._matches_scope(
        {"user_id": "", "agent_id": "", "session_id": ""},
        user_id="user_1",
        agent_id="agent_1",
        session_id="sess_1",
    )
    assert not service._matches_scope(
        {"user_id": "user_2", "agent_id": "", "session_id": ""},
        user_id="user_1",
        agent_id="agent_1",
        session_id="sess_1",
    )


def test_ontology_context_mismatch_summarizes_runtime_graph_status():
    service = GraphMemoryService(
        db_manager=_FakeDbManager(),
        runtime_raw_ingestor=_FakeIngestor(),
        semantic_agent_flow=_FakeSemanticFlow(),
    )
    calls = []

    def _recording_query(database, cypher, params, workspace_id="default"):
        calls.append(
            {
                "database": database,
                "cypher": cypher,
                "params": params,
                "workspace_id": workspace_id,
            }
        )
        return [
        {
            "indexed_context_hashes": ["old", "new"],
            "indexed_ontology_ids": ["legacy"],
            "indexed_profiles": ["vocabulary.v1"],
            "scoped_nodes": 4,
            "missing_context_nodes": 0,
            "missing_context_hash_nodes": 1,
        }
        ]

    service._run_query = _recording_query

    with patch.object(memory_service_mod.graph_registry, "find_by_database") as mock_find:
        mock_find.return_value = type(
            "Target",
            (),
            {
                "graph_id": "kgfinance",
                "ontology_id": "finance",
                "vocabulary_profile": "vocabulary.v2",
            },
        )()
        payload = service.ontology_context_mismatch(
            workspace_id="default",
            databases=["kgnormal"],
        )

    assert payload["mismatch"] is True
    assert payload["missing_context"] is True
    status = payload["databases"][0]
    assert status["database"] == "kgnormal"
    assert status["graph_id"] == "kgfinance"
    assert "multiple_indexed_context_hashes" in status["mismatch_reasons"]
    assert "indexed_ontology_id_differs_from_target" in status["mismatch_reasons"]
    assert "OPTIONAL MATCH (n:Document)" in calls[0]["cypher"]


def test_run_query_prefers_query_proxy_with_workspace_scope():
    query_proxy = _RecordingQueryProxy()
    service = GraphMemoryService(
        db_manager=_FakeDbManager(),
        runtime_raw_ingestor=_FakeIngestor(),
        semantic_agent_flow=_FakeSemanticFlow(),
        query_proxy=query_proxy,
    )

    rows = service._run_query(
        "kgnormal",
        "MATCH (m:Document) RETURN m",
        {"limit": 5},
        workspace_id="ws-runtime",
    )

    assert rows == [{"memory_id": "mem_1"}]
    request = query_proxy.calls[0]
    assert request.workspace_id == "ws-runtime"
    assert request.database == "kgnormal"
    assert request.params == {"limit": 5}


def test_search_memories_surfaces_ontology_context_mismatch():
    service = GraphMemoryService(
        db_manager=_FakeDbManager(),
        runtime_raw_ingestor=_FakeIngestor(),
        semantic_agent_flow=_FakeSemanticFlow(),
    )
    service.ontology_context_mismatch = lambda **_: {"mismatch": False, "databases": []}
    service._search_document_fallback = lambda **_: []

    payload = service.search_memories(
        workspace_id="default",
        query="Who manages Seoul retail?",
        limit=3,
    )

    assert payload["ontology_context_mismatch"] == {"mismatch": False, "databases": []}
    assert payload["semantic_context"]["ontology_context_mismatch"]["mismatch"] is False


def test_archive_memory_tries_candidate_databases_until_it_finds_a_match():
    service = GraphMemoryService(
        db_manager=_FakeDbManager(),
        runtime_raw_ingestor=_FakeIngestor(),
        semantic_agent_flow=_FakeSemanticFlow(),
        default_database="ladybug_local",
    )
    calls = []

    service._candidate_databases = lambda database=None: ["ladybug_local", "kgnormal"]

    def _archive(database, memory_id, workspace_id, archived_at):  # noqa: ANN001
        calls.append((database, memory_id, workspace_id, archived_at))
        return 0 if database == "ladybug_local" else 3

    service._archive_memory_in_db = _archive

    payload = service.archive_memory(memory_id="mem_1", workspace_id="default")

    assert [call[0] for call in calls] == ["ladybug_local", "kgnormal"]
    assert payload["memory_id"] == "mem_1"
    assert payload["database"] == "kgnormal"
    assert payload["status"] == "archived"
    assert payload["archived_nodes"] == 3
