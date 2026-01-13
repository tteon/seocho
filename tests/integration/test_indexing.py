"""
Integration tests for Indexing workflows.

Tests:
- GraphAgent Indexing (Neo4j LPG and RDF)
- HybridAgent Indexing (LanceDB)
"""
import pytest
import json
import os
from pathlib import Path


class TestGraphAgentIndexing:
    """Test Neo4j LPG and RDF indexing."""
    
    def test_import_modules(self):
        """Verify core indexing modules can be imported."""
        from src.core.graphagent_indexing import Neo4jGraphManager, LPGLoader, RDFLoader
        assert Neo4jGraphManager is not None
        assert LPGLoader is not None
        assert RDFLoader is not None
    
    def test_data_file_exists(self):
        """Verify the kgbuild-traces.json file exists."""
        data_file = Path("/workspace/kgbuild-traces.json")
        assert data_file.exists(), "kgbuild-traces.json not found"
        
        with open(data_file, 'r') as f:
            data = json.load(f)
        
        assert isinstance(data, list), "Data should be a list of traces"
        assert len(data) > 0, "Data should not be empty"
    
    @pytest.mark.skipif(
        os.getenv("NEO4J_URI") is None,
        reason="Neo4j not configured"
    )
    def test_neo4j_connectivity(self):
        """Test Neo4j database connectivity."""
        from neo4j import GraphDatabase
        
        uri = os.getenv("NEO4J_URI", "bolt://graphrag-neo4j:7687")
        auth = (os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password"))
        
        driver = GraphDatabase.driver(uri, auth=auth)
        driver.verify_connectivity()
        driver.close()
    
    @pytest.mark.skipif(
        os.getenv("NEO4J_URI") is None,
        reason="Neo4j not configured"
    )
    def test_lpg_database_exists(self):
        """Verify LPG database exists and has data."""
        from neo4j import GraphDatabase
        
        uri = os.getenv("NEO4J_URI", "bolt://graphrag-neo4j:7687")
        auth = (os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password"))
        
        driver = GraphDatabase.driver(uri, auth=auth)
        
        with driver.session(database="lpg") as session:
            result = session.run("MATCH (n) RETURN count(n) AS count LIMIT 1")
            count = result.single()["count"]
            assert count > 0, "LPG database should have nodes"
        
        driver.close()
    
    @pytest.mark.skipif(
        os.getenv("NEO4J_URI") is None,
        reason="Neo4j not configured"
    )
    def test_rdf_database_exists(self):
        """Verify RDF database exists and has data."""
        from neo4j import GraphDatabase
        
        uri = os.getenv("NEO4J_URI", "bolt://graphrag-neo4j:7687")
        auth = (os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password"))
        
        driver = GraphDatabase.driver(uri, auth=auth)
        
        with driver.session(database="rdf") as session:
            result = session.run("MATCH (n) RETURN count(n) AS count LIMIT 1")
            count = result.single()["count"]
            assert count > 0, "RDF database should have nodes"
        
        driver.close()


class TestHybridAgentIndexing:
    """Test LanceDB hybrid indexing."""
    
    def test_import_modules(self):
        """Verify hybrid indexing modules can be imported."""
        from src.core.hybridagent_indexing import build_hybrid_index, get_embedding
        assert build_hybrid_index is not None
        assert get_embedding is not None
    
    def test_lancedb_path_exists(self):
        """Verify LanceDB directory exists."""
        db_path = Path("/workspace/workspace/data/lancedb")
        assert db_path.exists(), "LanceDB directory not found"
    
    def test_lancedb_table_exists(self):
        """Verify the fibo_context table exists."""
        import lancedb
        
        db = lancedb.connect("/workspace/workspace/data/lancedb")
        table_names = db.table_names()
        
        assert "fibo_context" in table_names, "fibo_context table not found"
    
    def test_lancedb_has_data(self):
        """Verify the table has indexed data."""
        import lancedb
        
        db = lancedb.connect("/workspace/workspace/data/lancedb")
        table = db.open_table("fibo_context")
        
        count = table.count_rows()
        assert count > 0, "Table should have indexed data"
    
    def test_embedding_generation(self):
        """Test embedding generation."""
        from src.core.hybridagent_indexing import get_embedding
        
        test_text = "What is a credit derivative?"
        embedding = get_embedding(test_text)
        
        assert isinstance(embedding, list), "Embedding should be a list"
        assert len(embedding) == 1536, "Embedding dimension should be 1536"
        assert all(isinstance(x, float) for x in embedding), "All values should be floats"
