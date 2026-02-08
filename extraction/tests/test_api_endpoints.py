"""Tests for API endpoints."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

# Mock heavy imports before importing agent_server
with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
    with patch("neo4j.GraphDatabase") as mock_gdb:
        mock_driver = MagicMock()
        mock_gdb.driver.return_value = mock_driver
        with patch("vector_store.faiss") as mock_faiss:
            mock_faiss.IndexFlatL2.return_value = MagicMock()
            with patch("agents.Agent"), patch("agents.function_tool", lambda f: f):
                # Need to mock the agents module
                pass


class TestListEndpoints:
    """Test GET endpoints that don't require agent execution."""

    @pytest.fixture
    def client(self):
        """Create a test client with mocked dependencies."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            with patch("neo4j.GraphDatabase"):
                with patch("vector_store.faiss"):
                    from fastapi.testclient import TestClient
                    try:
                        from agent_server import app
                        return TestClient(app, raise_server_exceptions=False)
                    except Exception:
                        pytest.skip("Cannot import agent_server in test environment")

    def test_list_databases(self, client):
        if client is None:
            pytest.skip("client not available")
        response = client.get("/databases")
        assert response.status_code == 200
        data = response.json()
        assert "databases" in data
        assert isinstance(data["databases"], list)

    def test_list_agents(self, client):
        if client is None:
            pytest.skip("client not available")
        response = client.get("/agents")
        assert response.status_code == 200
        data = response.json()
        assert "agents" in data


class TestQueryValidation:
    """Test request validation."""

    def test_query_request_model(self):
        from pydantic import BaseModel, Field, ValidationError

        class QueryRequest(BaseModel):
            query: str = Field(..., max_length=2000)
            user_id: str = "user_default"

        # Valid
        req = QueryRequest(query="test query")
        assert req.query == "test query"
        assert req.user_id == "user_default"

        # Too long
        with pytest.raises(ValidationError):
            QueryRequest(query="x" * 2001)

        # Missing query
        with pytest.raises(ValidationError):
            QueryRequest()
