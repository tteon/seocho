"""Tests for AgentFactory database-scoped agent provisioning.

See ``test_agents_runtime.py`` for the rationale on snapshot+restore
around the import-time ``sys.modules['agents']`` mutation. Same
pattern applies here (seocho-eug0).
"""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

fake_agents = types.SimpleNamespace(
    Agent=object,
    function_tool=lambda fn: fn,
    RunContextWrapper=object,
)
sys.modules["agents"] = fake_agents

import agent_factory


def teardown_module(module):
    """Pop the agents stub so subsequent test files re-resolve from disk."""

    sys.modules.pop("agents", None)
    for cached in ("agent_factory", "extraction.agent_factory"):
        sys.modules.pop(cached, None)


class _DummyAgent:
    def __init__(self, name: str, instructions: str, tools: list):
        self.name = name
        self.instructions = instructions
        self.tools = tools


class _DummyConnector:
    def __init__(self):
        self.calls = []

    def run_cypher(self, query: str, database: str = "neo4j", graph_id: str | None = None) -> str:
        self.calls.append({"query": query, "database": database, "graph_id": graph_id})
        return f"{graph_id or database}:{query}"


def test_create_agents_for_all_graphs_skips_unavailable(monkeypatch):
    monkeypatch.setattr(agent_factory, "Agent", _DummyAgent)
    monkeypatch.setattr(agent_factory, "function_tool", lambda fn: fn)
    monkeypatch.setattr(
        agent_factory.graph_registry,
        "list_graph_ids",
        lambda: ["kgnormal", "kgfibo"],
    )
    graph_map = {
        "kgnormal": agent_factory.GraphTarget(graph_id="kgnormal", database="kgnormal"),
        "kgfibo": agent_factory.GraphTarget(graph_id="kgfibo", database="kgfibo"),
    }
    monkeypatch.setattr(
        agent_factory.graph_registry,
        "get_graph",
        lambda graph_id: graph_map.get(graph_id),
    )

    factory = agent_factory.AgentFactory(_DummyConnector())

    class _DbManager:
        @staticmethod
        def get_graph_schema_info(graph_id: str) -> str:
            if graph_id == "kgfibo":
                raise RuntimeError("Graph not found: kgfibo")
            return f"schema:{graph_id}"

    statuses = factory.create_agents_for_all_graphs(_DbManager())

    assert factory.list_agents() == ["kgnormal"]
    assert factory.get_agent("kgfibo") is None
    assert statuses == [
        {"graph": "kgnormal", "database": "kgnormal", "status": "ready", "reason": "created"},
        {"graph": "kgfibo", "database": "kgfibo", "status": "degraded", "reason": "Graph not found: kgfibo"},
    ]


def test_create_agents_for_all_graphs_marks_cached_as_checked(monkeypatch):
    monkeypatch.setattr(agent_factory, "Agent", _DummyAgent)
    monkeypatch.setattr(agent_factory, "function_tool", lambda fn: fn)
    monkeypatch.setattr(
        agent_factory.graph_registry,
        "list_graph_ids",
        lambda: ["kgnormal"],
    )
    monkeypatch.setattr(
        agent_factory.graph_registry,
        "get_graph",
        lambda graph_id: agent_factory.GraphTarget(graph_id=graph_id, database=graph_id),
    )

    factory = agent_factory.AgentFactory(_DummyConnector())

    class _DbManager:
        @staticmethod
        def get_graph_schema_info(graph_id: str) -> str:
            return f"schema:{graph_id}"

    first_statuses = factory.create_agents_for_all_graphs(_DbManager())
    second_statuses = factory.create_agents_for_all_graphs(_DbManager())

    assert first_statuses == [{"graph": "kgnormal", "database": "kgnormal", "status": "ready", "reason": "created"}]
    assert second_statuses == [{"graph": "kgnormal", "database": "kgnormal", "status": "ready", "reason": "checked"}]


def test_create_graph_agent_binds_query_to_graph(monkeypatch):
    monkeypatch.setattr(agent_factory, "Agent", _DummyAgent)
    monkeypatch.setattr(agent_factory, "function_tool", lambda fn: fn)
    connector = _DummyConnector()
    factory = agent_factory.AgentFactory(connector)

    target = agent_factory.GraphTarget(
        graph_id="finance",
        database="kgfibo",
        ontology_id="fibo",
        vocabulary_profile="vocabulary.v2",
        description="Finance graph",
    )
    agent = factory.create_graph_agent(target, "schema:finance")

    query_tool = next(tool for tool in agent.tools if tool.__name__ == "query_graph")
    out = query_tool(types.SimpleNamespace(context=types.SimpleNamespace(shared_memory=None)), "MATCH (n) RETURN n")

    assert out == "finance:MATCH (n) RETURN n"
    assert connector.calls == [
        {
            "query": "MATCH (n) RETURN n",
            "database": "kgfibo",
            "graph_id": "finance",
        }
    ]
    assert "fibo" in agent.instructions


# ---------------------------------------------------------------------------
# Phase 2 — ontology context bridge to runtime tools
# ---------------------------------------------------------------------------


class _OntologyContextStub:
    """Mimics CompiledOntologyContext.descriptor.context_hash for tests."""

    def __init__(self, context_hash: str) -> None:
        self.descriptor = types.SimpleNamespace(context_hash=context_hash)


class _SkewProbeConnector(_DummyConnector):
    """Connector that exposes a typed ``query`` surface for skew probing."""

    def __init__(self, *, indexed_hashes):
        super().__init__()
        self._indexed_hashes = list(indexed_hashes)

    def query(self, cypher: str, *, params=None, database: str = "neo4j"):
        return [
            {
                "raw_context_hashes": list(self._indexed_hashes),
                "indexed_context_hashes": list(self._indexed_hashes),
            }
        ]


def test_detect_ontology_skew_returns_none_when_context_missing():
    result = agent_factory._detect_ontology_skew(
        connector=_DummyConnector(),
        graph_id="g1",
        database="kgnormal",
        workspace_id="default",
        ontology_context=None,
    )
    assert result is None


def test_detect_ontology_skew_returns_none_when_hashes_match():
    connector = _SkewProbeConnector(indexed_hashes=["hashA"])
    result = agent_factory._detect_ontology_skew(
        connector=connector,
        graph_id="g1",
        database="kgnormal",
        workspace_id="default",
        ontology_context=_OntologyContextStub("hashA"),
    )
    assert result is None


def test_detect_ontology_skew_reports_drift():
    connector = _SkewProbeConnector(indexed_hashes=["hashA"])
    result = agent_factory._detect_ontology_skew(
        connector=connector,
        graph_id="g1",
        database="kgnormal",
        workspace_id="acme",
        ontology_context=_OntologyContextStub("hashB"),
    )
    assert result is not None
    assert result["active_context_hash"] == "hashB"
    assert result["indexed_context_hashes"] == ["hashA"]
    assert result["graph_id"] == "g1"
    assert result["database"] == "kgnormal"
    assert result["workspace_id"] == "acme"


def test_create_graph_agent_without_context_preserves_current_behavior(monkeypatch):
    """Backward compatibility: callers that don't pass ontology_context get the legacy flow."""

    monkeypatch.setattr(agent_factory, "Agent", _DummyAgent)
    monkeypatch.setattr(agent_factory, "function_tool", lambda fn: fn)
    factory = agent_factory.AgentFactory(_DummyConnector())
    target = agent_factory.GraphTarget(graph_id="finance", database="kgfibo")
    agent = factory.create_graph_agent(target, "schema:finance")

    assert getattr(agent, "ontology_context_skew") is None
    query_tool = next(tool for tool in agent.tools if tool.__name__ == "query_graph")
    out = query_tool(
        types.SimpleNamespace(context=types.SimpleNamespace(shared_memory=None)),
        "MATCH (n) RETURN n",
    )
    assert out == "finance:MATCH (n) RETURN n"


def test_create_graph_agent_with_matching_context_works(monkeypatch):
    monkeypatch.setattr(agent_factory, "Agent", _DummyAgent)
    monkeypatch.setattr(agent_factory, "function_tool", lambda fn: fn)
    connector = _SkewProbeConnector(indexed_hashes=["hashA"])
    factory = agent_factory.AgentFactory(connector)
    target = agent_factory.GraphTarget(graph_id="finance", database="kgfibo")

    agent = factory.create_graph_agent(
        target,
        "schema:finance",
        ontology_context=_OntologyContextStub("hashA"),
        workspace_id="acme",
    )

    assert getattr(agent, "ontology_context_skew") is None
    query_tool = next(tool for tool in agent.tools if tool.__name__ == "query_graph")
    out = query_tool(
        types.SimpleNamespace(context=types.SimpleNamespace(shared_memory=None)),
        "MATCH (n) RETURN n",
    )
    assert out == "finance:MATCH (n) RETURN n"


def test_create_graph_agent_with_skewed_context_refuses_tool_calls(monkeypatch):
    """The structural property: tools refuse to answer when graph hash drifts from agent hash."""

    import json as _json

    monkeypatch.setattr(agent_factory, "Agent", _DummyAgent)
    monkeypatch.setattr(agent_factory, "function_tool", lambda fn: fn)
    connector = _SkewProbeConnector(indexed_hashes=["hashOld"])
    factory = agent_factory.AgentFactory(connector)
    target = agent_factory.GraphTarget(graph_id="finance", database="kgfibo")

    agent = factory.create_graph_agent(
        target,
        "schema:finance",
        ontology_context=_OntologyContextStub("hashNew"),
        workspace_id="acme",
    )

    skew = getattr(agent, "ontology_context_skew")
    assert skew is not None
    assert skew["active_context_hash"] == "hashNew"
    assert skew["indexed_context_hashes"] == ["hashOld"]

    for tool_name in ("query_graph", "get_schema", "get_graph_profile"):
        tool = next(t for t in agent.tools if t.__name__ == tool_name)
        if tool_name == "query_graph":
            payload = tool(
                types.SimpleNamespace(context=types.SimpleNamespace(shared_memory=None)),
                "MATCH (n) RETURN n",
            )
        else:
            payload = tool()
        parsed = _json.loads(payload)
        assert parsed["error"] == "ontology_context_mismatch"
        assert parsed["graph_id"] == "finance"
        assert parsed["database"] == "kgfibo"
        assert parsed["active_context_hash"] == "hashNew"
        assert parsed["indexed_context_hashes"] == ["hashOld"]

    # Connector's run_cypher must not have been called — refusal is hard.
    assert connector.calls == []


def test_create_agents_for_graphs_surfaces_skew_in_status(monkeypatch):
    """Phase 1 told the world about the mismatch via the response; Phase 2 surfaces
    it inside the per-agent status entry too, so the readiness summary picks it up."""

    monkeypatch.setattr(agent_factory, "Agent", _DummyAgent)
    monkeypatch.setattr(agent_factory, "function_tool", lambda fn: fn)
    monkeypatch.setattr(
        agent_factory.graph_registry,
        "list_graph_ids",
        lambda: ["finance"],
    )
    monkeypatch.setattr(
        agent_factory.graph_registry,
        "get_graph",
        lambda graph_id: agent_factory.GraphTarget(graph_id=graph_id, database=graph_id),
    )
    connector = _SkewProbeConnector(indexed_hashes=["hashOld"])
    factory = agent_factory.AgentFactory(connector)

    class _DbManager:
        @staticmethod
        def get_graph_schema_info(graph_id: str) -> str:
            return f"schema:{graph_id}"

    statuses = factory.create_agents_for_graphs(
        ["finance"],
        _DbManager(),
        ontology_contexts={"finance": _OntologyContextStub("hashNew")},
        workspace_id="acme",
    )

    assert len(statuses) == 1
    entry = statuses[0]
    assert entry["graph"] == "finance"
    assert entry["status"] == "degraded"
    assert entry["reason"] == "ontology_context_mismatch"
    assert "ontology_context_mismatch" in entry
    assert entry["ontology_context_mismatch"]["active_context_hash"] == "hashNew"
    assert entry["ontology_context_mismatch"]["indexed_context_hashes"] == ["hashOld"]
