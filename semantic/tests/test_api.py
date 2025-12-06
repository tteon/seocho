import pytest
from fastapi.testclient import TestClient
from main import app
from unittest.mock import MagicMock

client = TestClient(app)

# Mock dependencies to avoid real DB/Kafka calls during unit tests
app.dependency_overrides = {}

def test_ingest_endpoint(mocker):
    # Mock the Neo4j client and Agent
    mocker.patch('main.neo4j_client.create_document_node')
    mocker.patch('main.neo4j_client.create_relationship')
    mocker.patch('main.agent.process_document', return_value=[{"name": "Test", "type": "Concept"}])
    
    payload = [
        {"id": "test_1", "content": "This is a test document.", "source": "test_suite"}
    ]
    
    response = client.post("/ingest", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "success", "processed": 1}
