import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ontology_hints_builder import build_hints_from_records


def test_build_hints_from_records_merges_aliases_and_keywords():
    records = [
        {
            "canonical": "Neo4j",
            "aliases": ["Neo4J", "Neo4-j"],
            "keywords": ["graph", "database"],
        },
        {
            "canonical": "GraphRAG",
            "aliases": ["Graph RAG"],
            "keywords": ["retrieval", "graph"],
        },
    ]
    payload = build_hints_from_records(records)

    assert payload["aliases"]["neo4j"] == "Neo4j"
    assert payload["aliases"]["neo4 j"] == "Neo4j"
    assert "database" in payload["label_keywords"]["neo4j"]
    assert "graph" in payload["label_keywords"]["graphrag"]
