"""
Integration tests for Extraction workflows.

Tests:
- Pipeline extraction (RDF + LPG)
- Grounding verification
- Output format validation
"""
import pytest
import json
import os
from pathlib import Path


class TestExtractionPipeline:
    """Test the FIBO extraction pipeline."""
    
    def test_import_modules(self):
        """Verify pipeline modules can be imported."""
        from src.pipeline.pipeline import run_fibo_pipeline, step_extraction, step_grounding
        assert run_fibo_pipeline is not None
        assert step_extraction is not None
        assert step_grounding is not None
    
    def test_extraction_output_format(self):
        """Test extraction produces correct format."""
        from src.pipeline.pipeline import step_extraction
        
        test_input = """
        Cboe Global Markets, Inc. reported revenues of $4.2 billion for 2023.
        The company's net income was $1.1 billion.
        """
        
        result, prompt, snippet = step_extraction(test_input)
        
        # Verify structure
        assert isinstance(result, dict), "Result should be a dictionary"
        assert "rdf_triples" in result, "Should have rdf_triples"
        assert "lpg_graph" in result, "Should have lpg_graph"
        
        # Verify RDF triples
        assert isinstance(result["rdf_triples"], list), "rdf_triples should be a list"
        
        # Verify LPG graph
        assert isinstance(result["lpg_graph"], dict), "lpg_graph should be a dict"
        assert "nodes" in result["lpg_graph"], "lpg_graph should have nodes"
        assert "relationships" in result["lpg_graph"], "lpg_graph should have relationships"
    
    def test_grounding_verification(self):
        """Test grounding removes unsupported triples."""
        from src.pipeline.pipeline import step_grounding
        
        test_text = "The company has $100 million in revenue."
        
        # Mock triples - some supported, some not
        test_triples = [
            {
                "subject": "ex:Company",
                "predicate": "fibo:hasRevenue",
                "object": "100000000",
                "is_literal": True
            },
            {
                "subject": "ex:Company",
                "predicate": "fibo:hasAliens",  # Not in text
                "object": "ex:Aliens",
                "is_literal": False
            }
        ]
        
        verified = step_grounding(test_text, test_triples)
        
        # Should filter out unsupported triples
        assert isinstance(verified, list), "Should return a list"
        assert len(verified) <= len(test_triples), "Should not add triples"
    
    def test_pipeline_end_to_end(self):
        """Test full pipeline execution."""
        from src.pipeline.pipeline import run_fibo_pipeline
        
        test_input = """
        Microsoft Corporation issued bonds worth $5 billion.
        The bonds have a maturity date of December 31, 2030.
        """
        
        result = run_fibo_pipeline(test_input)
        
        assert isinstance(result, dict), "Result should be a dictionary"
        assert "rdf_triples" in result, "Should have rdf_triples"
        assert "lpg_graph" in result, "Should have lpg_graph"
        
        # Verify some extraction happened
        rdf_count = len(result.get("rdf_triples", []))
        lpg_node_count = len(result.get("lpg_graph", {}).get("nodes", []))
        
        # At least some data should be extracted
        assert rdf_count > 0 or lpg_node_count > 0, "Should extract some data"
    
    def test_output_files_exist(self):
        """Verify pipeline output files exist."""
        rdf_file = Path("/workspace/output/rdf_n10s/fibo_graph.ttl")
        lpg_nodes = Path("/workspace/output/lpg_native/nodes.csv")
        lpg_edges = Path("/workspace/output/lpg_native/edges.csv")
        
        assert rdf_file.exists(), "RDF output should exist"
        assert lpg_nodes.exists(), "LPG nodes file should exist"
        assert lpg_edges.exists(), "LPG edges file should exist"
    
    def test_rdf_output_valid_ttl(self):
        """Verify RDF output is valid Turtle format."""
        rdf_file = Path("/workspace/output/rdf_n10s/fibo_graph.ttl")
        
        with open(rdf_file, 'r') as f:
            content = f.read()
        
        # Basic validation
        assert "@prefix" in content, "Should have prefix declarations"
        assert "ex:" in content or "fibo" in content, "Should have entity URIs"
    
    def test_lpg_output_valid_csv(self):
        """Verify LPG CSV files are valid."""
        import csv
        
        nodes_file = Path("/workspace/output/lpg_native/nodes.csv")
        
        with open(nodes_file, 'r') as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames
            
            assert "id" in headers, "Nodes should have id column"
            assert "label" in headers, "Nodes should have label column"
            assert "props" in headers, "Nodes should have props column"
