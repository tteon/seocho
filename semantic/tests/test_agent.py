import pytest
from agent import GraphAgent

def test_agent_processing():
    agent = GraphAgent()
    content = "GraphRAG uses Neo4j and LLMs."
    entities = agent.process_document(content)
    
    assert isinstance(entities, list)
    # Based on our mock logic
    names = [e["name"] for e in entities]
    assert "GraphRAG" in names
    assert "Neo4j" in names
    assert "LLM" in names
