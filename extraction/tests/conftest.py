"""
Shared test fixtures for SEOCHO extraction tests.
"""

import os
import sys
import pytest
from unittest.mock import MagicMock, patch

# Ensure extraction/ is on the path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True, scope="session")
def test_env():
    """Set environment variables for the test session."""
    env_overrides = {
        "OPENAI_API_KEY": "test-key-not-real",
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "testpassword",
        # Disable Opik in tests
        "OPIK_URL_OVERRIDE": "",
    }
    with patch.dict(os.environ, env_overrides):
        yield


@pytest.fixture
def mock_neo4j_driver():
    """Mock Neo4j driver/session/result chain."""
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)

    # Default: session.run returns empty result
    mock_result = MagicMock()
    mock_result.__iter__ = MagicMock(return_value=iter([]))
    session.run.return_value = mock_result
    session.execute_write = MagicMock()

    return driver, session


@pytest.fixture
def mock_openai_client():
    """Mock OpenAI client for chat completions and embeddings."""
    client = MagicMock()

    # Mock chat completions
    chat_response = MagicMock()
    chat_response.choices = [MagicMock()]
    chat_response.choices[0].message.content = '{"nodes": [], "relationships": []}'
    client.chat.completions.create.return_value = chat_response

    # Mock embeddings
    embed_response = MagicMock()
    embed_response.data = [MagicMock()]
    embed_response.data[0].embedding = [0.1] * 1536
    client.embeddings.create.return_value = embed_response

    return client


@pytest.fixture
def sample_extraction_result():
    """Standard extraction result with nodes and relationships."""
    return {
        "nodes": [
            {
                "id": "node_1",
                "label": "Company",
                "properties": {"name": "Acme Corp", "industry": "Tech"},
            },
            {
                "id": "node_2",
                "label": "Person",
                "properties": {"name": "Jane Smith", "role": "CEO"},
            },
        ],
        "relationships": [
            {
                "source": "node_2",
                "target": "node_1",
                "type": "WORKS_AT",
                "properties": {"since": "2020"},
            }
        ],
    }


@pytest.fixture
def sample_data_source_items():
    """Standard data source items for pipeline tests."""
    return [
        {
            "id": "item_1",
            "content": "Acme Corp is a leading technology company.",
            "category": "general",
            "source": "test",
            "metadata": {},
        },
        {
            "id": "item_2",
            "content": "Jane Smith is the CEO of Acme Corp.",
            "category": "general",
            "source": "test",
            "metadata": {},
        },
    ]
