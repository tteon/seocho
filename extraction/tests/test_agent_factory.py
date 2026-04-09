"""Tests for AgentFactory database-scoped agent provisioning."""

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
