"""Tests for agent-level SDK: Session, tools, agents, session tracing."""

from __future__ import annotations

import json
import sys
from types import ModuleType
from typing import Any, Dict, List, Optional, Sequence
from unittest.mock import MagicMock

import pytest

from seocho.models import Memory
from seocho.ontology import Ontology, NodeDef, RelDef, P
from seocho.tracing import (
    SessionTrace, begin_session, enable_tracing, disable_tracing,
    is_tracing_enabled,
)


# ======================================================================
# Fixtures
# ======================================================================

def _make_test_ontology() -> Ontology:
    return Ontology(
        name="test_finance",
        description="Test ontology for finance",
        nodes={
            "Company": NodeDef(
                description="A company entity",
                properties={"name": P(required=True), "industry": P()},
            ),
            "Person": NodeDef(
                description="A person entity",
                properties={"name": P(required=True), "role": P()},
            ),
        },
        relationships={
            "EMPLOYS": RelDef(
                source="Company",
                target="Person",
                properties={"since": P()},
            ),
        },
    )


class FakeLLMResponse:
    def __init__(self, text: str, usage: Optional[Dict[str, int]] = None):
        self.text = text
        self.model = "fake-model"
        self.usage = usage or {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}

    def json(self):
        return json.loads(self.text)


class FakeLLM:
    """Minimal LLM backend for testing."""

    model = "fake-model"

    def __init__(self, responses: Optional[List[str]] = None):
        self._responses = responses or []
        self._call_index = 0

    def complete(self, *, system: str, user: str, temperature: float = 0.0,
                 max_tokens: Optional[int] = None,
                 response_format: Optional[Dict[str, Any]] = None) -> FakeLLMResponse:
        if self._responses:
            text = self._responses[min(self._call_index, len(self._responses) - 1)]
            self._call_index += 1
            return FakeLLMResponse(text)
        # Default: return extraction with nodes
        return FakeLLMResponse(json.dumps({
            "nodes": [
                {"label": "Company", "id": "c1", "properties": {"name": "TestCorp"}},
            ],
            "relationships": [],
        }))

    async def acomplete(self, **kwargs) -> FakeLLMResponse:
        return self.complete(**kwargs)

    def to_agents_sdk_model(self, *, model: Optional[str] = None):
        """Return a fake agents SDK model."""
        try:
            from agents.models.interface import Model

            class _FakeModel(Model):
                async def get_response(self, *a, **kw):
                    raise NotImplementedError("fake model")

                async def stream_response(self, *a, **kw):
                    raise NotImplementedError("fake model")

            return _FakeModel()
        except ImportError:
            return MagicMock()


class FakeGraphStore:
    """Minimal graph store for testing."""

    def __init__(self):
        self.written: List[Dict[str, Any]] = []
        self.queries: List[str] = []

    def write(self, nodes=None, relationships=None, *, ontology=None,
              database="neo4j", source_id="", workspace_id="", **kwargs) -> Dict[str, Any]:
        nodes = nodes or []
        relationships = relationships or []
        self.written.append({
            "nodes": nodes, "relationships": relationships,
            "database": database, "source_id": source_id,
        })
        return {"nodes_created": len(nodes), "relationships_created": len(relationships), "errors": []}

    def query(self, cypher: str, *, params=None, database="neo4j") -> List[Dict[str, Any]]:
        self.queries.append(cypher)
        return [{"name": "TestCorp", "industry": "Tech"}]

    def get_schema(self, *, database="neo4j") -> Dict[str, Any]:
        return {"labels": ["Company", "Person"], "relationship_types": ["EMPLOYS"]}


class FakePipelineEngine:
    def add(self, content: str, *, database: str = "neo4j", category: str = "general", metadata=None):
        return Memory(
            memory_id="mem-123",
            workspace_id="default",
            content=content,
            metadata={
                "nodes_created": 2,
                "relationships_created": 1,
                "chunks_processed": 1,
                "validation_errors": [],
                "write_errors": [],
            },
            status="active",
            database=database,
            category=category,
        )

    def ask(self, question: str, *, database: str = "neo4j", reasoning_mode=None, repair_budget=None):
        return f"pipeline answer for {question}"


# ======================================================================
# Session tracing tests
# ======================================================================

class TestSessionTrace:

    def test_begin_session_creates_trace(self):
        enable_tracing(backend="console")
        try:
            trace = begin_session("test-123", "my-session")
            assert isinstance(trace, SessionTrace)
            assert trace.session_id == "test-123"
            assert "my-session" in trace.name
        finally:
            disable_tracing()

    def test_session_trace_logs_spans(self):
        trace = SessionTrace("s1", "test")
        trace.log_span("step1", input_data={"a": 1}, output_data={"b": 2})
        trace.log_span("step2", input_data={"c": 3})
        assert len(trace.spans) == 2
        assert trace.spans[0]["name"] == "step1"
        assert trace.spans[1]["name"] == "step2"

    def test_session_trace_end_returns_summary(self):
        trace = SessionTrace("s2", "test")
        trace.log_span("op1")
        trace.log_span("op2")
        summary = trace.end()
        assert summary["session_id"] == "s2"
        assert summary["total_spans"] == 2
        assert "elapsed_seconds" in summary


# ======================================================================
# Tool creation tests
# ======================================================================

class TestToolCreation:

    def test_create_indexing_tools_returns_list(self, monkeypatch):
        self._patch_agents(monkeypatch)
        from seocho.tools import create_indexing_tools

        onto = _make_test_ontology()
        llm = FakeLLM()
        store = FakeGraphStore()

        tools = create_indexing_tools(ontology=onto, graph_store=store, llm=llm)
        assert len(tools) == 5  # extract, validate, score, link, write

    def test_create_query_tools_returns_list(self, monkeypatch):
        self._patch_agents(monkeypatch)
        from seocho.tools import create_query_tools

        onto = _make_test_ontology()
        store = FakeGraphStore()

        tools = create_query_tools(ontology=onto, graph_store=store)
        assert len(tools) == 2  # text2cypher, execute_cypher

    def test_create_query_tools_with_vector_store(self, monkeypatch):
        self._patch_agents(monkeypatch)
        from seocho.tools import create_query_tools

        onto = _make_test_ontology()
        store = FakeGraphStore()
        vstore = MagicMock()

        tools = create_query_tools(ontology=onto, graph_store=store, vector_store=vstore)
        assert len(tools) == 3  # text2cypher, execute_cypher, search_similar

    @staticmethod
    def _patch_agents(monkeypatch):
        """Ensure agents module is available (already installed, but guard)."""
        try:
            import agents  # noqa
        except ImportError:
            pytest.skip("openai-agents not installed")


# ======================================================================
# Session tests (pipeline fallback — no real agent runner)
# ======================================================================

class TestSession:

    def test_session_pipeline_fallback_add(self):
        onto = _make_test_ontology()
        llm = FakeLLM()
        store = FakeGraphStore()

        from seocho.session import Session

        sess = Session(
            name="test", ontology=onto, graph_store=store, llm=llm,
            database="testdb",
        )

        result = sess.add("Samsung is a company.", )
        assert result["ok"] is True
        assert result["mode"] == "pipeline"
        assert result["degraded"] is False
        assert sess.context.total_nodes >= 0
        assert sess.context.indexed_sources[0]["mode"] == "pipeline"

    def test_session_pipeline_fallback_ask(self):
        onto = _make_test_ontology()
        llm = FakeLLM(responses=[
            # Intent extraction
            json.dumps({"intent": "entity_lookup", "anchor_entity": "TestCorp", "anchor_label": "Company"}),
            # Answer synthesis
            "TestCorp is in the Tech industry.",
        ])
        store = FakeGraphStore()

        from seocho.session import Session

        sess = Session(
            name="test", ontology=onto, graph_store=store, llm=llm,
            database="testdb",
        )
        answer = sess.ask("What industry is TestCorp in?", )
        assert "TestCorp" in answer or "Tech" in answer
        assert sess.context.queries[0]["mode"] == "pipeline"
        assert sess.context.queries[0]["degraded"] is False

    def test_session_agent_add_fallback_records_degraded_metadata(self, monkeypatch):
        onto = _make_test_ontology()
        llm = FakeLLM()
        store = FakeGraphStore()

        from seocho.session import Session

        fake_agents = ModuleType("agents")

        class _FailingRunner:
            @staticmethod
            async def run(agent, user_msg):
                raise RuntimeError("agent backend unavailable")

        fake_agents.Runner = _FailingRunner
        monkeypatch.setitem(sys.modules, "agents", fake_agents)
        monkeypatch.setattr(Session, "_get_indexing_agent", lambda self: object())
        monkeypatch.setattr(Session, "_get_pipeline_engine", lambda self: FakePipelineEngine())

        from seocho.agent_config import AgentConfig
        sess = Session(
            name="fallback-add", ontology=onto, graph_store=store, llm=llm,
            database="testdb",
            agent_config=AgentConfig(execution_mode="agent"),
        )

        result = sess.add("Samsung is a company.")
        assert result["mode"] == "pipeline"
        assert result["degraded"] is True
        assert result["fallback_from"] == "agent"
        assert "unavailable" in result["fallback_reason"]
        assert sess.context.indexed_sources[0]["degraded"] is True
        assert sess.context.indexed_sources[0]["fallback_from"] == "agent"

    def test_session_agent_query_fallback_records_degraded_metadata(self, monkeypatch):
        onto = _make_test_ontology()
        llm = FakeLLM()
        store = FakeGraphStore()

        from seocho.session import Session

        fake_agents = ModuleType("agents")

        class _FailingRunner:
            @staticmethod
            async def run(agent, user_msg):
                raise RuntimeError("query agent unavailable")

        fake_agents.Runner = _FailingRunner
        monkeypatch.setitem(sys.modules, "agents", fake_agents)
        monkeypatch.setattr(Session, "_get_query_agent", lambda self: object())
        monkeypatch.setattr(Session, "_get_pipeline_engine", lambda self: FakePipelineEngine())

        from seocho.agent_config import AgentConfig
        sess = Session(
            name="fallback-query", ontology=onto, graph_store=store, llm=llm,
            database="testdb",
            agent_config=AgentConfig(execution_mode="agent"),
        )

        answer = sess.ask("What industry is TestCorp in?")
        assert "pipeline answer" in answer
        assert sess.context.queries[0]["mode"] == "pipeline"
        assert sess.context.queries[0]["degraded"] is True
        assert sess.context.queries[0]["fallback_from"] == "agent"
        summary = sess.close()
        assert summary["degraded_operations"] == 1

    def test_session_context_tracks_operations(self):
        onto = _make_test_ontology()
        llm = FakeLLM()
        store = FakeGraphStore()

        from seocho.session import Session

        sess = Session(
            name="tracking", ontology=onto, graph_store=store, llm=llm,
            database="testdb",
        )

        sess.add("Doc 1", )
        sess.add("Doc 2", )
        assert len(sess.context.indexed_sources) == 2

        summary = sess.context.summary()
        assert "2 document" in summary

    def test_session_close_returns_summary(self):
        onto = _make_test_ontology()
        llm = FakeLLM()
        store = FakeGraphStore()

        from seocho.session import Session

        sess = Session(
            name="closing", ontology=onto, graph_store=store, llm=llm,
            database="testdb",
        )
        sess.add("Some text", )
        summary = sess.close()
        assert summary["session_id"] == sess.session_id
        assert summary["indexed_documents"] == 1

        # Can't use after close
        with pytest.raises(RuntimeError, match="closed"):
            sess.add("more", )

    def test_session_context_manager(self):
        onto = _make_test_ontology()
        llm = FakeLLM()
        store = FakeGraphStore()

        from seocho.session import Session

        with Session(
            name="ctx", ontology=onto, graph_store=store, llm=llm,
            database="testdb",
        ) as sess:
            sess.add("text", )
            assert not sess._closed

        assert sess._closed

    def test_session_repr(self):
        onto = _make_test_ontology()
        llm = FakeLLM()
        store = FakeGraphStore()

        from seocho.session import Session

        sess = Session(
            name="repr_test", ontology=onto, graph_store=store, llm=llm,
            database="testdb",
        )
        r = repr(sess)
        assert "repr_test" in r
        assert "active" in r


# ======================================================================
# Agent creation tests
# ======================================================================

class TestAgentCreation:

    def test_create_indexing_agent(self, monkeypatch):
        try:
            import agents  # noqa
        except ImportError:
            pytest.skip("openai-agents not installed")

        onto = _make_test_ontology()
        llm = FakeLLM()
        store = FakeGraphStore()

        from seocho.agents import create_indexing_agent

        agent = create_indexing_agent(
            ontology=onto, graph_store=store, llm=llm,
        )
        assert agent.name == "IndexingAgent"
        assert len(agent.tools) == 5

    def test_create_query_agent(self, monkeypatch):
        try:
            import agents  # noqa
        except ImportError:
            pytest.skip("openai-agents not installed")

        onto = _make_test_ontology()
        llm = FakeLLM()
        store = FakeGraphStore()

        from seocho.agents import create_query_agent

        agent = create_query_agent(
            ontology=onto, graph_store=store, llm=llm,
        )
        assert agent.name == "QueryAgent"
        assert len(agent.tools) == 2

    def test_indexing_agent_system_prompt_contains_ontology(self):
        onto = _make_test_ontology()
        from seocho.agents import _indexing_system_prompt
        prompt = _indexing_system_prompt(onto)
        assert "Company" in prompt
        assert "Person" in prompt
        assert "EMPLOYS" in prompt

    def test_query_agent_system_prompt_contains_ontology(self):
        onto = _make_test_ontology()
        from seocho.agents import _query_system_prompt
        prompt = _query_system_prompt(onto)
        assert "entity_lookup" in prompt
        assert "text2cypher" in prompt
