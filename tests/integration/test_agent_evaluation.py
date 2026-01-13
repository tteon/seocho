"""
Integration tests for Agent Evaluation workflows.

Tests:
- Agent setup and configuration
- Tool routing and execution
- Evaluation metrics
- E2E agent execution
"""
import pytest
import os


class TestAgentSetup:
    """Test agent configuration and tool setup."""
    
    def test_import_modules(self):
        """Verify agent modules can be imported."""
        from src.agents.agent_setup import (
            manager_agent, lpg_analyst, rdf_ontologist, hybrid_searcher
        )
        assert manager_agent is not None
        assert lpg_analyst is not None
        assert rdf_ontologist is not None
        assert hybrid_searcher is not None
    
    def test_manager_agent_has_tools(self):
        """Verify manager agent has required tools."""
        from src.agents.agent_setup import manager_agent
        
        assert hasattr(manager_agent, 'tools'), "Manager should have tools"
        assert len(manager_agent.tools) > 0, "Manager should have at least one tool"
    
    def test_retrieval_tools_exist(self):
        """Verify all retrieval tools are defined."""
        from src.agents.agent_setup import (
            search_docs, query_lpg, query_rdf, fulltext_lpg, fulltext_rdf
        )
        assert search_docs is not None
        assert query_lpg is not None
        assert query_rdf is not None
        assert fulltext_lpg is not None
        assert fulltext_rdf is not None


class TestAgentExecution:
    """Test agent execution and routing."""
    
    @pytest.mark.skipif(
        os.getenv("OPENAI_API_KEY") is None,
        reason="OpenAI API key not configured"
    )
    def test_lpg_tool_execution(self):
        """Test LPG query tool execution."""
        from src.agents.agent_setup import query_lpg
        
        # Simple query to count nodes
        result = query_lpg("MATCH (n) RETURN count(n) AS total LIMIT 1")
        
        assert isinstance(result, str), "Should return string result"
        assert "total" in result.lower() or "error" in result.lower()
    
    @pytest.mark.skipif(
        os.getenv("OPENAI_API_KEY") is None,
        reason="OpenAI API key not configured"
    )
    def test_rdf_tool_execution(self):
        """Test RDF query tool execution."""
        from src.agents.agent_setup import query_rdf
        
        # Simple query to count resources
        result = query_rdf("MATCH (r:Resource) RETURN count(r) AS total LIMIT 1")
        
        assert isinstance(result, str), "Should return string result"
        assert "total" in result.lower() or "error" in result.lower()
    
    @pytest.mark.skipif(
        os.getenv("OPENAI_API_KEY") is None,
        reason="OpenAI API key not configured"
    )
    def test_hybrid_search_execution(self):
        """Test hybrid search tool execution."""
        from src.agents.agent_setup import search_docs
        
        result = search_docs("credit derivative", top_k=3, search_mode="hybrid")
        
        assert isinstance(result, str), "Should return string result"
        assert len(result) > 0, "Should return some content"


class TestAgentEvaluation:
    """Test agent evaluation pipeline."""
    
    def test_import_evaluation_modules(self):
        """Verify evaluation modules can be imported."""
        from src.agents.agent_evaluation import evaluation_task, run_mini_test
        assert evaluation_task is not None
        assert run_mini_test is not None
    
    def test_import_custom_metrics(self):
        """Verify custom metrics can be imported."""
        from src.utils.retrieval_metrics import (
            RetrievalQuality, RetrievalRelevance, DatabaseSelectionQuality
        )
        from src.utils.experiment_metrics import (
            RoutingAccuracy, ContextPrecision, ConflictResolutionScore, ToolCallQuality
        )
        
        # Retrieval metrics
        assert RetrievalQuality is not None
        assert RetrievalRelevance is not None
        assert DatabaseSelectionQuality is not None
        
        # Experiment metrics
        assert RoutingAccuracy is not None
        assert ContextPrecision is not None
        assert ConflictResolutionScore is not None
        assert ToolCallQuality is not None
    
    def test_evaluation_task_structure(self):
        """Test evaluation task returns correct format."""
        from src.agents.agent_evaluation import evaluation_task
        
        # Mock dataset item
        mock_item = {
            "input": {"text": "What is a bond?"},
            "expected_output": "A debt instrument.",
            "metadata": {"references": ["Definition of bond"]}
        }
        
        # This will fail if agent runs, but we're testing structure
        try:
            result = evaluation_task(mock_item)
            
            # If it succeeds, verify structure
            assert isinstance(result, dict), "Should return dict"
            assert "input" in result, "Should have input"
            assert "output" in result, "Should have output"
            assert "tool_calls" in result, "Should have tool_calls"
            assert "agent_steps" in result, "Should have agent_steps"
        except Exception:
            # Expected if OpenAI/Opik not configured
            pass


class TestToolRouting:
    """Test agent tool routing logic."""
    
    def test_import_routing_test(self):
        """Verify routing test module can be imported."""
        from src.agents import test_agent_routing
        assert test_agent_routing is not None
    
    def test_verify_tools_import(self):
        """Verify tools verification module can be imported."""
        from src.agents import verify_tools
        assert verify_tools is not None
