"""Tests for API endpoints."""

import importlib
import os
import sys
import types
from contextlib import nullcontext
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(scope="module")
def app_module():
    """Import agent_server with heavy runtime dependencies mocked."""
    mock_graph_db = MagicMock()
    mock_graph_db.driver.return_value = MagicMock()
    fake_neo4j = types.ModuleType("neo4j")
    fake_neo4j.GraphDatabase = mock_graph_db
    fake_neo4j_exceptions = types.ModuleType("neo4j.exceptions")
    fake_neo4j_exceptions.ServiceUnavailable = RuntimeError
    fake_neo4j_exceptions.SessionExpired = RuntimeError
    fake_faiss = MagicMock()
    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = MagicMock()
    
    class DummyAgent:
        def __init__(self, *args, **kwargs):
            self.name = kwargs.get("name", "DummyAgent")
            self.instructions = kwargs.get("instructions", "")
            self.tools = kwargs.get("tools", [])
            self.handoffs = kwargs.get("handoffs", [])

    class DummyRunner:
        @staticmethod
        async def run(*args, **kwargs):
            return types.SimpleNamespace(final_output="", to_input_list=lambda: [])

    def function_tool(func):
        return func

    class DummyRunContextWrapper:
        pass

    fake_agents = types.SimpleNamespace(
        Agent=DummyAgent,
        Runner=DummyRunner,
        function_tool=function_tool,
        RunContextWrapper=DummyRunContextWrapper,
        trace=lambda *args, **kwargs: nullcontext(),
    )

    with patch.dict(
        os.environ,
        {
            "OPENAI_API_KEY": "test-key",
            "NEO4J_URI": "bolt://localhost:7687",
            "NEO4J_USER": "neo4j",
            "NEO4J_PASSWORD": "password",
            "OPIK_URL_OVERRIDE": "",
        },
        clear=False,
    ):
        with patch.dict(
            sys.modules,
            {
                "neo4j": fake_neo4j,
                "neo4j.exceptions": fake_neo4j_exceptions,
                "faiss": fake_faiss,
                "openai": fake_openai,
                "agents": fake_agents,
            },
        ):
            import agent_server

            return importlib.reload(agent_server)


@pytest.fixture
async def client(app_module):
    transport = httpx.ASGITransport(app=app_module.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


@pytest.mark.anyio
class TestListEndpoints:
    """Test endpoints without external DB/runtime dependencies."""

    async def test_list_databases(self, client):
        response = await client.get("/databases")
        assert response.status_code == 200
        data = response.json()
        assert "databases" in data
        assert isinstance(data["databases"], list)

    async def test_list_graphs(self, client):
        response = await client.get("/graphs")
        assert response.status_code == 200
        data = response.json()
        assert "graphs" in data
        assert isinstance(data["graphs"], list)

    async def test_list_agents(self, client):
        response = await client.get("/agents")
        assert response.status_code == 200
        data = response.json()
        assert "agents" in data

    async def test_runtime_health_endpoint(self, client):
        response = await client.get("/health/runtime")
        assert response.status_code == 200
        payload = response.json()
        assert payload["scope"] == "runtime"
        assert "components" in payload

    async def test_batch_health_endpoint(self, client):
        response = await client.get("/health/batch")
        assert response.status_code == 200
        payload = response.json()
        assert payload["scope"] == "batch"
        assert payload["status"] in {"ready", "degraded", "blocked"}

    async def test_run_agent_semantic_endpoint(self, client, app_module):
        with patch.object(app_module.semantic_agent_flow, "run") as mock_run:
            mock_run.return_value = {
                "response": "Route selected: LPG.",
                "trace_steps": [],
                "route": "lpg",
                "semantic_context": {"entities": ["Neo4j"], "matches": {}, "unresolved_entities": []},
                "lpg_result": {"mode": "lpg", "summary": "", "records": []},
                "rdf_result": None,
            }
            response = await client.post(
                "/run_agent_semantic",
                json={"query": "Tell me about Neo4j", "workspace_id": "default"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["route"] == "lpg"
            _, kwargs = mock_run.call_args
            assert kwargs["workspace_id"] == "default"

    async def test_run_agent_semantic_with_overrides(self, client, app_module):
        with patch.object(app_module.semantic_agent_flow, "run") as mock_run:
            mock_run.return_value = {
                "response": "Route selected: LPG.",
                "trace_steps": [],
                "route": "lpg",
                "semantic_context": {
                    "entities": ["Neo4j"],
                    "matches": {"Neo4j": [{"source": "override"}]},
                    "unresolved_entities": [],
                    "overrides_applied": {"Neo4j": {"database": "kgnormal", "node_id": 1}},
                },
                "lpg_result": {"mode": "lpg", "summary": "", "records": []},
                "rdf_result": None,
            }
            response = await client.post(
                "/run_agent_semantic",
                json={
                    "query": "Tell me about Neo4j",
                    "workspace_id": "default",
                    "databases": ["kgnormal"],
                    "entity_overrides": [
                        {"question_entity": "Neo4j", "database": "kgnormal", "node_id": 1}
                    ],
                },
            )
            assert response.status_code == 200
            payload = response.json()
            assert "overrides_applied" in payload["semantic_context"]
            _, kwargs = mock_run.call_args
            assert kwargs["workspace_id"] == "default"

    async def test_fulltext_ensure_endpoint(self, client, app_module):
        with patch.object(app_module, "ensure_fulltext_indexes_impl") as mock_impl:
            mock_impl.return_value = {
                "results": [
                    {
                        "database": "kgnormal",
                        "index_name": "entity_fulltext",
                        "exists": True,
                        "created": False,
                        "state": "ONLINE",
                        "labels": ["Entity"],
                        "properties": ["name"],
                        "message": "Index already exists.",
                    }
                ]
            }
            response = await client.post(
                "/indexes/fulltext/ensure",
                json={"workspace_id": "default", "databases": ["kgnormal"]},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["results"][0]["database"] == "kgnormal"

    async def test_rules_assess_endpoint(self, client, app_module):
        with patch.object(app_module, "assess_rule_profile") as mock_assess:
            mock_assess.return_value = {
                "workspace_id": "default",
                "rule_profile": {"schema_version": "rules.v1", "rules": []},
                "shacl_like": {"schema_version": "rules.v1", "shapes": []},
                "validation_summary": {"total_nodes": 2, "passed_nodes": 2, "failed_nodes": 0},
                "violation_breakdown": [],
                "export_preview": {"schema_version": "rules.v1", "statements": [], "unsupported_rules": []},
                "practical_readiness": {
                    "status": "ready",
                    "score": 1.0,
                    "pass_ratio": 1.0,
                    "enforceable_ratio": 1.0,
                    "failed_nodes": 0,
                    "total_nodes": 2,
                    "total_rules": 0,
                    "unsupported_rules": 0,
                    "recommendations": ["You can apply exported Cypher constraints and keep /rules/validate in ingestion CI."],
                    "top_violations": [],
                },
            }
            response = await client.post(
                "/rules/assess",
                json={"workspace_id": "default", "graph": {"nodes": [], "relationships": []}},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["practical_readiness"]["status"] == "ready"

    async def test_rules_export_shacl_endpoint(self, client, app_module):
        with patch.object(app_module, "export_rule_profile_to_shacl") as mock_export:
            mock_export.return_value = {
                "workspace_id": "default",
                "schema_version": "rules.v1",
                "shapes": [{"shape_id": "CompanyShape", "target_class": "Company", "properties": []}],
                "turtle": "@prefix sh: <http://www.w3.org/ns/shacl#> .\n",
                "unsupported_rules": [],
            }
            response = await client.post(
                "/rules/export/shacl",
                json={"workspace_id": "default", "rule_profile": {"schema_version": "rules.v1", "rules": []}},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["schema_version"] == "rules.v1"
            assert isinstance(payload["shapes"], list)

    async def test_semantic_artifact_draft_create_endpoint(self, client, app_module):
        with patch.object(app_module, "create_semantic_artifact_draft") as mock_create:
            mock_create.return_value = {
                "workspace_id": "default",
                "artifact_id": "sa_1",
                "name": "draft1",
                "status": "draft",
                "created_at": "2026-01-01T00:00:00Z",
                "approved_at": None,
                "approved_by": None,
                "approval_note": None,
                "source_summary": {},
                "ontology_candidate": {"ontology_name": "x", "classes": [], "relationships": []},
                "shacl_candidate": {"shapes": []},
            }
            response = await client.post(
                "/semantic/artifacts/drafts",
                json={
                    "workspace_id": "default",
                    "name": "draft1",
                    "ontology_candidate": {"ontology_name": "x", "classes": [], "relationships": []},
                    "shacl_candidate": {"shapes": []},
                },
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["status"] == "draft"

    async def test_semantic_artifact_approve_endpoint(self, client, app_module):
        with patch.object(app_module, "approve_semantic_artifact_draft") as mock_approve:
            mock_approve.return_value = {
                "workspace_id": "default",
                "artifact_id": "sa_1",
                "name": "draft1",
                "status": "approved",
                "created_at": "2026-01-01T00:00:00Z",
                "approved_at": "2026-01-01T01:00:00Z",
                "approved_by": "reviewer",
                "approval_note": "ok",
                "source_summary": {},
                "ontology_candidate": {"ontology_name": "x", "classes": [], "relationships": []},
                "shacl_candidate": {"shapes": []},
            }
            response = await client.post(
                "/semantic/artifacts/sa_1/approve",
                json={
                    "workspace_id": "default",
                    "approved_by": "reviewer",
                    "approval_note": "ok",
                },
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["status"] == "approved"

    async def test_semantic_artifact_deprecate_endpoint(self, client, app_module):
        with patch.object(app_module, "deprecate_semantic_artifact_approved") as mock_deprecate:
            mock_deprecate.return_value = {
                "workspace_id": "default",
                "artifact_id": "sa_1",
                "name": "draft1",
                "status": "deprecated",
                "created_at": "2026-01-01T00:00:00Z",
                "approved_at": "2026-01-01T01:00:00Z",
                "approved_by": "reviewer",
                "approval_note": "ok",
                "deprecated_at": "2026-01-01T02:00:00Z",
                "deprecated_by": "reviewer",
                "deprecation_note": "superseded",
                "source_summary": {},
                "ontology_candidate": {"ontology_name": "x", "classes": [], "relationships": []},
                "shacl_candidate": {"shapes": []},
                "vocabulary_candidate": {"schema_version": "vocabulary.v2", "profile": "skos", "terms": []},
            }
            response = await client.post(
                "/semantic/artifacts/sa_1/deprecate",
                json={
                    "workspace_id": "default",
                    "deprecated_by": "reviewer",
                    "deprecation_note": "superseded",
                },
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["status"] == "deprecated"

    async def test_semantic_artifact_list_endpoint(self, client, app_module):
        with patch.object(app_module, "read_semantic_artifacts") as mock_list:
            mock_list.return_value = {
                "workspace_id": "default",
                "artifacts": [{"artifact_id": "sa_1", "status": "draft"}],
            }
            response = await client.get("/semantic/artifacts?workspace_id=default&status=draft")
            assert response.status_code == 200
            payload = response.json()
            assert payload["artifacts"][0]["artifact_id"] == "sa_1"

    async def test_semantic_artifact_get_endpoint(self, client, app_module):
        with patch.object(app_module, "read_semantic_artifact") as mock_get:
            mock_get.return_value = {
                "workspace_id": "default",
                "artifact_id": "sa_1",
                "name": "draft1",
                "status": "draft",
                "created_at": "2026-01-01T00:00:00Z",
                "approved_at": None,
                "approved_by": None,
                "approval_note": None,
                "source_summary": {},
                "ontology_candidate": {"ontology_name": "x", "classes": [], "relationships": []},
                "shacl_candidate": {"shapes": []},
            }
            response = await client.get("/semantic/artifacts/sa_1?workspace_id=default")
            assert response.status_code == 200
            payload = response.json()
            assert payload["artifact_id"] == "sa_1"

    async def test_platform_chat_send_endpoint(self, client, app_module):
        with patch.object(app_module.backend_specialist_agent, "execute", new_callable=AsyncMock) as mock_execute:
            with patch.object(app_module.frontend_specialist_agent, "build_ui_payload") as mock_ui:
                mock_execute.return_value = {
                    "response": "platform response",
                    "trace_steps": [{"type": "GENERATION", "agent": "A", "content": "x", "metadata": {}}],
                }
                mock_ui.return_value = {"cards": [], "trace_summary": {}, "entity_candidates": []}
                response = await client.post(
                    "/platform/chat/send",
                    json={
                        "session_id": "s1",
                        "message": "hello",
                        "mode": "semantic",
                        "workspace_id": "default",
                    },
                )
                assert response.status_code == 200
                data = response.json()
                assert data["session_id"] == "s1"
                assert data["assistant_message"] == "platform response"

    async def test_run_debate_returns_blocked_state_when_no_ready_agents(self, client, app_module):
        with patch.object(app_module.graph_registry, "list_graph_ids", return_value=["kgnormal"]):
            with patch.object(app_module.graph_registry, "is_valid_graph", return_value=True):
                with patch.object(app_module.agent_factory, "create_agents_for_graphs") as mock_create:
                    with patch.object(app_module.agent_factory, "get_agents_for_graphs") as mock_get_agents:
                        mock_create.return_value = [
                            {
                                "graph": "kgnormal",
                                "database": "kgnormal",
                                "status": "degraded",
                                "reason": "Graph not found",
                            }
                        ]
                        mock_get_agents.return_value = {}
                        response = await client.post(
                            "/run_debate",
                            json={
                                "query": "compare entities",
                                "workspace_id": "default",
                                "user_id": "u1",
                                "graph_ids": ["kgnormal"],
                            },
                        )
                        assert response.status_code == 200
                        payload = response.json()
                        assert payload["debate_state"] == "blocked"
                        assert payload["degraded"] is True
                        assert payload["debate_results"] == []

    async def test_run_debate_rejects_invalid_graph(self, client, app_module):
        with patch.object(app_module.graph_registry, "list_graph_ids", return_value=["kgnormal"]):
            with patch.object(app_module.graph_registry, "is_valid_graph", return_value=False):
                response = await client.post(
                    "/run_debate",
                    json={
                        "query": "compare entities",
                        "workspace_id": "default",
                        "user_id": "u1",
                        "graph_ids": ["missing"],
                    },
                )
                assert response.status_code == 400
                assert "Invalid graph" in response.text

    async def test_platform_raw_ingest_endpoint(self, client, app_module):
        mock_ingestor = MagicMock()
        with patch.object(app_module, "get_runtime_raw_ingestor", return_value=mock_ingestor):
            mock_ingest = mock_ingestor.ingest_records
            mock_ingest.return_value = {
                "target_database": "kgnormal",
                "records_received": 2,
                "records_processed": 2,
                "records_failed": 0,
                "total_nodes": 5,
                "total_relationships": 3,
                "status": "success",
                "errors": [],
            }
            response = await client.post(
                "/platform/ingest/raw",
                json={
                    "workspace_id": "default",
                    "target_database": "kgnormal",
                    "records": [
                        {"id": "r1", "content": "Alpha acquires Beta."},
                        {"id": "r2", "content": "Beta serves Alpha."},
                    ],
                },
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["status"] == "success"
            assert payload["records_processed"] == 2

    async def test_platform_raw_ingest_with_approved_artifact_id(self, client, app_module):
        mock_ingestor = MagicMock()
        with patch.object(app_module, "get_runtime_raw_ingestor", return_value=mock_ingestor):
            with patch.object(app_module, "resolve_approved_artifact_payload") as mock_resolve:
                mock_resolve.return_value = {
                    "ontology_candidate": {"ontology_name": "approved", "classes": [], "relationships": []},
                    "shacl_candidate": {"shapes": []},
                }
                mock_ingestor.ingest_records.return_value = {
                    "target_database": "kgnormal",
                    "records_received": 1,
                    "records_processed": 1,
                    "records_failed": 0,
                    "total_nodes": 3,
                    "total_relationships": 1,
                    "status": "success",
                    "errors": [],
                }
                response = await client.post(
                    "/platform/ingest/raw",
                    json={
                        "workspace_id": "default",
                        "target_database": "kgnormal",
                        "semantic_artifact_policy": "approved_only",
                        "approved_artifact_id": "sa_approved_1",
                        "records": [{"id": "r1", "content": "Alpha acquires Beta."}],
                    },
                )
                assert response.status_code == 200
                assert mock_resolve.call_count == 1
                args, kwargs = mock_ingestor.ingest_records.call_args
                assert kwargs["workspace_id"] == "default"
                assert kwargs["semantic_artifact_policy"] == "approved_only"
                assert kwargs["approved_artifacts"]["ontology_candidate"]["ontology_name"] == "approved"

    async def test_public_create_memory_endpoint(self, client, app_module):
        with patch.object(app_module.memory_service, "create_memory") as mock_create:
            mock_create.return_value = {
                "memory": {
                    "memory_id": "mem_1",
                    "workspace_id": "default",
                    "user_id": "user_1",
                    "agent_id": "agent_1",
                    "session_id": "sess_1",
                    "content": "Alice manages Seoul retail.",
                    "metadata": {"source": "note"},
                    "status": "stored",
                    "created_at": "2026-03-12T00:00:00+00:00",
                    "updated_at": "2026-03-12T00:00:00+00:00",
                    "database": "kgnormal",
                },
                "ingest_summary": {"database": "kgnormal", "entities_detected": 2, "relations_detected": 1},
            }
            response = await client.post(
                "/api/memories",
                json={
                    "workspace_id": "default",
                    "user_id": "user_1",
                    "agent_id": "agent_1",
                    "session_id": "sess_1",
                    "content": "Alice manages Seoul retail.",
                    "metadata": {"source": "note"},
                },
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["memory"]["memory_id"] == "mem_1"
            assert payload["trace_id"]

    async def test_public_create_memory_resolves_approved_artifact(self, client, app_module):
        with patch.object(app_module.memory_service, "create_memory") as mock_create:
            with patch.object(app_module, "resolve_approved_artifact_payload") as mock_resolve:
                mock_resolve.return_value = {
                    "ontology_candidate": {"ontology_name": "approved", "classes": [], "relationships": []},
                    "shacl_candidate": {"shapes": []},
                    "vocabulary_candidate": {"schema_version": "vocabulary.v2", "profile": "skos", "terms": []},
                }
                mock_create.return_value = {
                    "memory": {
                        "memory_id": "mem_2",
                        "workspace_id": "default",
                        "content": "Approved memory",
                        "metadata": {},
                        "status": "stored",
                        "created_at": "2026-03-12T00:00:00+00:00",
                        "updated_at": "2026-03-12T00:00:00+00:00",
                        "database": "kgnormal",
                    },
                    "ingest_summary": {"database": "kgnormal", "entities_detected": 1, "relations_detected": 0},
                }
                response = await client.post(
                    "/api/memories",
                    json={
                        "workspace_id": "default",
                        "content": "Approved memory",
                        "approved_artifact_id": "sa_approved_1",
                    },
                )
                assert response.status_code == 200
                assert mock_resolve.call_count == 1
                _, kwargs = mock_create.call_args
                assert kwargs["approved_artifacts"]["ontology_candidate"]["ontology_name"] == "approved"

    async def test_public_memory_search_endpoint(self, client, app_module):
        with patch.object(app_module.memory_service, "search_memories") as mock_search:
            mock_search.return_value = {
                "results": [
                    {
                        "memory_id": "mem_1",
                        "content": "Alice manages Seoul retail.",
                        "content_preview": "Alice manages Seoul retail.",
                        "metadata": {"source": "note"},
                        "score": 0.93,
                        "reasons": ["entity_match", "fulltext"],
                        "matched_entities": ["Seoul"],
                        "database": "kgnormal",
                        "status": "active",
                    }
                ],
                "semantic_context": {"entities": ["Seoul"], "matches": {}, "unresolved_entities": []},
            }
            response = await client.post(
                "/api/memories/search",
                json={"workspace_id": "default", "query": "Who manages Seoul retail?", "limit": 3},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["results"][0]["memory_id"] == "mem_1"
            assert payload["trace_id"]

    async def test_public_memory_get_endpoint(self, client, app_module):
        with patch.object(app_module.memory_service, "get_memory") as mock_get:
            mock_get.return_value = {
                "memory_id": "mem_1",
                "workspace_id": "default",
                "content": "Alice manages Seoul retail.",
                "content_preview": "Alice manages Seoul retail.",
                "metadata": {"source": "note"},
                "status": "active",
                "created_at": "2026-03-12T00:00:00+00:00",
                "updated_at": "2026-03-12T00:00:00+00:00",
                "database": "kgnormal",
                "entities": [{"id": "n1", "labels": ["Person"], "name": "Alice"}],
            }
            response = await client.get("/api/memories/mem_1?workspace_id=default")
            assert response.status_code == 200
            payload = response.json()
            assert payload["memory"]["memory_id"] == "mem_1"
            assert payload["memory"]["entities"][0]["name"] == "Alice"

    async def test_public_memory_archive_endpoint(self, client, app_module):
        with patch.object(app_module.memory_service, "archive_memory") as mock_archive:
            mock_archive.return_value = {
                "memory_id": "mem_1",
                "workspace_id": "default",
                "database": "kgnormal",
                "status": "archived",
                "archived_at": "2026-03-12T01:00:00+00:00",
                "archived_nodes": 3,
            }
            response = await client.delete("/api/memories/mem_1?workspace_id=default")
            assert response.status_code == 200
            payload = response.json()
            assert payload["status"] == "archived"
            assert payload["archived_nodes"] == 3

    async def test_public_memory_chat_endpoint(self, client, app_module):
        with patch.object(app_module.graph_registry, "get_graph") as mock_get_graph:
            with patch.object(app_module.memory_service, "chat_from_memories") as mock_chat:
                mock_get_graph.return_value = types.SimpleNamespace(database="kgnormal")
                mock_chat.return_value = {
                    "assistant_message": "Alice manages Seoul retail.",
                    "memory_hits": [{"memory_id": "mem_1", "score": 0.93, "database": "kgnormal"}],
                    "search_results": [
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
                response = await client.post(
                    "/api/chat",
                    json={
                        "workspace_id": "default",
                        "message": "What do we know about Seoul retail?",
                        "graph_ids": ["kgnormal"],
                    },
                )
                assert response.status_code == 200
                payload = response.json()
                assert payload["assistant_message"] == "Alice manages Seoul retail."
                assert payload["memory_hits"][0]["memory_id"] == "mem_1"
                _, kwargs = mock_chat.call_args
                assert kwargs["databases"] == ["kgnormal"]


class TestQueryValidation:
    """Test request validation."""

    def test_query_request_model(self):
        from pydantic import BaseModel, Field, ValidationError

        class QueryRequest(BaseModel):
            query: str = Field(..., max_length=2000)
            user_id: str = "user_default"

        req = QueryRequest(query="test query")
        assert req.query == "test query"
        assert req.user_id == "user_default"

        with pytest.raises(ValidationError):
            QueryRequest(query="x" * 2001)

        with pytest.raises(ValidationError):
            QueryRequest()


    def test_execute_cypher_tool_enforces_tool_budget(self, app_module):
        wrapper = types.SimpleNamespace(
            context=app_module.ServerContext(
                user_id="user_default",
                allowed_databases=["kgnormal"],
                tool_budget=1,
            )
        )

        with patch.object(app_module.neo4j_conn, "run_cypher", return_value='[{"ok": 1}]') as mock_run:
            first = app_module.execute_cypher_tool(wrapper, "RETURN 1", database="kgnormal")
            second = app_module.execute_cypher_tool(wrapper, "RETURN 1", database="kgnormal")

        assert first == '[{"ok": 1}]'
        assert "Tool budget exhausted" in second
        mock_run.assert_called_once_with("RETURN 1", database="kgnormal")
