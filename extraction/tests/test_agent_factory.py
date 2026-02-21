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
    def run_cypher(self, query: str, database: str = "neo4j") -> str:
        return f"{database}:{query}"


def test_create_agents_for_all_databases_skips_unavailable(monkeypatch):
    monkeypatch.setattr(agent_factory, "Agent", _DummyAgent)
    monkeypatch.setattr(agent_factory, "function_tool", lambda fn: fn)
    monkeypatch.setattr(
        agent_factory.db_registry,
        "list_databases",
        lambda: ["kgnormal", "kgfibo"],
    )

    factory = agent_factory.AgentFactory(_DummyConnector())

    class _DbManager:
        @staticmethod
        def get_schema_info(db_name: str) -> str:
            if db_name == "kgfibo":
                raise RuntimeError("Graph not found: kgfibo")
            return f"schema:{db_name}"

    statuses = factory.create_agents_for_all_databases(_DbManager())

    assert factory.list_agents() == ["kgnormal"]
    assert factory.get_agent("kgfibo") is None
    assert statuses == [
        {"database": "kgnormal", "status": "ready", "reason": "created"},
        {"database": "kgfibo", "status": "degraded", "reason": "Graph not found: kgfibo"},
    ]
