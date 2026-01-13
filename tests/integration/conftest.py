"""Integration test configuration and fixtures."""
import pytest
import os
from pathlib import Path


@pytest.fixture(scope="session")
def workspace_path():
    """Return the workspace path inside the container."""
    return Path("/workspace")


@pytest.fixture(scope="session")
def test_data_path(workspace_path):
    """Return the test data directory."""
    return workspace_path / "data"


@pytest.fixture(scope="session")
def neo4j_config():
    """Return Neo4j connection configuration."""
    return {
        "uri": os.getenv("NEO4J_URI", "bolt://graphrag-neo4j:7687"),
        "user": os.getenv("NEO4J_USER", "neo4j"),
        "password": os.getenv("NEO4J_PASSWORD", "password")
    }


@pytest.fixture(scope="session")
def lancedb_path():
    """Return LanceDB path."""
    return "/workspace/workspace/data/lancedb"


@pytest.fixture(scope="session")
def opik_config():
    """Return Opik configuration."""
    return {
        "url": os.getenv("OPIK_URL_OVERRIDE", "http://35.172.201.148:5173/api"),
        "project": os.getenv("OPIK_PROJECT_NAME", "graph-agent-evaluation")
    }


@pytest.fixture(scope="session")
def sample_extraction_input():
    """Return sample text for extraction testing."""
    return """
    Apple Inc. reported quarterly revenue of $89.5 billion.
    The company's CEO is Tim Cook.
    Apple operates in Cupertino, California.
    """


@pytest.fixture(scope="session")
def sample_dataset_item():
    """Return sample dataset item for evaluation testing."""
    return {
        "input": {"text": "What is the revenue of Apple Inc.?"},
        "expected_output": "Apple Inc. reported $89.5 billion in quarterly revenue.",
        "metadata": {
            "references": ["Financial statement showing Apple revenue"]
        }
    }
