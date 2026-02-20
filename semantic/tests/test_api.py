import pytest
import httpx
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

_MODULE_PATH = Path(__file__).resolve().parents[1] / "main.py"
sys.path.insert(0, str(_MODULE_PATH.parent))
mock_graph_db = MagicMock()
mock_graph_db.driver.return_value = MagicMock()
sys.modules["neo4j"] = types.SimpleNamespace(GraphDatabase=mock_graph_db)
_SPEC = importlib.util.spec_from_file_location("semantic_main_for_tests", _MODULE_PATH)
semantic_main = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(semantic_main)
app = semantic_main.app

# Mock dependencies to avoid real DB/Kafka calls during unit tests
app.dependency_overrides = {}


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


@pytest.mark.anyio
async def test_ingest_endpoint(client):
    with patch.object(semantic_main.neo4j_client, "create_document_node"):
        with patch.object(semantic_main.neo4j_client, "create_relationship"):
            with patch.object(
                semantic_main.agent,
                "process_document",
                return_value=[{"name": "Test", "type": "Concept"}],
            ):
                payload = [
                    {"id": "test_1", "content": "This is a test document.", "source": "test_suite"}
                ]

                response = await client.post("/ingest", json=payload)
                assert response.status_code == 200
                assert response.json() == {"status": "success", "processed": 1}
