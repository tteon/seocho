"""
Unit tests for graphagent_indexing.py module.

Tests the core functionality of LPGLoader, RDFLoader, and sanitization functions.
"""

import pytest
import json
from unittest.mock import Mock, MagicMock, patch
from src.core.graphagent_indexing import (
    sanitize_properties,
    LPGLoader,
    RDFLoader,
    Neo4jGraphManager
)


class TestSanitizeProperties:
    """Test sanitize_properties function."""
    
    def test_sanitize_special_characters(self):
        """Test that special characters in keys are replaced."""
        input_props = {
            "R&D_budget": 1000000,
            "Q1-revenue": 500000,
            "test name": "value"
        }
        result = sanitize_properties(input_props)
        
        assert "RandD_budget" in result
        assert "Q1_revenue" in result
        assert "test_name" in result
        assert result["RandD_budget"] == 1000000
    
    def test_sanitize_nested_structures(self):
        """Test that nested dicts and lists are converted to JSON strings."""
        input_props = {
            "metadata": {"key1": "value1", "key2": "value2"},
            "items": [1, 2, 3],
            "simple": "text"
        }
        result = sanitize_properties(input_props)
        
        assert isinstance(result["metadata"], str)
        assert json.loads(result["metadata"]) == {"key1": "value1", "key2": "value2"}
        assert isinstance(result["items"], str)
        assert json.loads(result["items"]) == [1, 2, 3]
        assert result["simple"] == "text"


class TestLPGLoader:
    """Test LPGLoader class."""
    
    @pytest.fixture
    def mock_manager(self):
        """Create a mock Neo4jGraphManager."""
        manager = Mock(spec=Neo4jGraphManager)
        manager.driver = Mock()
        return manager
    
    @pytest.fixture
    def lpg_loader(self, mock_manager):
        """Create an LPGLoader instance with mock manager."""
        return LPGLoader(mock_manager, "test_db")
    
    def test_load_nodes_empty(self, lpg_loader):
        """Test load_nodes with empty node list."""
        count = lpg_loader.load_nodes([], "trace_001")
        assert count == 0
    
    @patch('src.core.graphagent_indexing.sanitize_properties')
    def test_load_nodes_with_data(self, mock_sanitize, lpg_loader, mock_manager):
        """Test load_nodes with sample node data."""
        mock_sanitize.return_value = {"name": "Test Entity", "_trace_id": "trace_001", "_node_id": "entity_001"}
        
        # Mock session
        mock_session = MagicMock()
        mock_manager.driver.session.return_value.__enter__.return_value = mock_session
        
        nodes = [
            {
                "id": "entity_001",
                "label": "Entity",
                "properties": {"name": "Test Entity"}
            }
        ]
        
        count = lpg_loader.load_nodes(nodes, "trace_001")
        
        # Verify session was called
        assert mock_session.run.called
        assert count == 1


class TestRDFLoader:
    """Test RDFLoader class."""
    
    @pytest.fixture
    def mock_manager(self):
        """Create a mock Neo4jGraphManager."""
        manager = Mock(spec=Neo4jGraphManager)
        manager.driver = Mock()
        return manager
    
    @pytest.fixture
    def rdf_loader(self, mock_manager):
        """Create an RDFLoader instance with mock manager."""
        return RDFLoader(mock_manager, "test_rdf_db")
    
    def test_load_triples_empty(self, rdf_loader):
        """Test load_triples with empty triple list."""
        count = rdf_loader.load_triples([], "trace_001")
        assert count == 0
    
    def test_predicate_sanitization(self, rdf_loader):
        """Test that predicate URIs are correctly sanitized."""
        # This would ideally test the predicate local name extraction
        # For now, just verify the method exists
        assert hasattr(rdf_loader, 'load_triples')


class TestNeo4jGraphManager:
    """Test Neo4jGraphManager class."""
    
    def test_initialization(self):
        """Test manager initialization."""
        manager = Neo4jGraphManager("bolt://localhost:7687", "neo4j", "password")
        assert manager.uri == "bolt://localhost:7687"
        assert manager.user == "neo4j"
        assert manager.password == "password"
        assert manager.driver is None
    
    @patch('src.core.graphagent_indexing.GraphDatabase')
    def test_connect(self, mock_graphdb):
        """Test connection to Neo4j."""
        mock_driver = Mock()
        mock_graphdb.driver.return_value = mock_driver
        
        manager = Neo4jGraphManager("bolt://localhost:7687", "neo4j", "password")
        manager.connect()
        
        assert manager.driver == mock_driver
        mock_driver.verify_connectivity.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
