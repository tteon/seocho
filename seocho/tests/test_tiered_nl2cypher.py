"""Tests for ADR-0090 tiered NL→Cypher tools + NLCypherExampleStore.

The function tools are decorated with ``@function_tool`` from the OpenAI
Agents SDK. We stub ``agents`` so ``function_tool`` returns the callable
unchanged (matching the test pattern used elsewhere in this repo, e.g.
test_agent_contract.py), then invoke the tools as plain functions.
"""

from __future__ import annotations

import importlib
import json
import sys
import types

import pytest


def _stub_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.SimpleNamespace(
        Agent=type("_StubAgent", (), {}),
        function_tool=lambda fn: fn,
        RunContextWrapper=type("_StubCtx", (), {}),
        Runner=type("_StubRunner", (), {}),
        ModelSettings=type("_StubSettings", (), {}),
        handoff=lambda *_a, **_kw: None,
        trace=lambda *_a, **_k: None,
    )
    monkeypatch.setitem(sys.modules, "agents", fake)
    if "seocho.tools" in sys.modules:
        del sys.modules["seocho.tools"]


# ---- NLCypherExampleStore (no Agents SDK dependency) ---------------------


def test_example_store_is_empty_by_default() -> None:
    from seocho.store.vector import NLCypherExampleStore

    store = NLCypherExampleStore()
    assert len(store) == 0
    assert store.search(workspace_id="ws", question="anything", k=5) == []


def test_example_store_adds_successful_pair() -> None:
    from seocho.store.vector import NLCypherExampleStore

    store = NLCypherExampleStore()
    store.add(
        workspace_id="ws",
        question="Find Apple",
        cypher="MATCH (e:Entity {name:'Apple'}) RETURN e",
    )
    assert len(store) == 1
    out = store.search(workspace_id="ws", question="Find Apple", k=5)
    assert len(out) == 1
    assert out[0].question == "Find Apple"
    assert "Apple" in out[0].cypher


def test_example_store_ignores_failed_pair() -> None:
    from seocho.store.vector import NLCypherExampleStore

    store = NLCypherExampleStore()
    store.add(workspace_id="ws", question="q", cypher="c", success=False)
    assert len(store) == 0


def test_example_store_is_workspace_scoped() -> None:
    from seocho.store.vector import NLCypherExampleStore

    store = NLCypherExampleStore()
    store.add(workspace_id="ws-A", question="qA", cypher="cypher A")
    store.add(workspace_id="ws-B", question="qB", cypher="cypher B")
    assert [ex.question for ex in store.search(workspace_id="ws-A", question="x", k=5)] == ["qA"]
    assert [ex.question for ex in store.search(workspace_id="ws-B", question="x", k=5)] == ["qB"]


def test_example_store_returns_most_recent_first() -> None:
    from seocho.store.vector import NLCypherExampleStore

    store = NLCypherExampleStore()
    for i in range(3):
        store.add(workspace_id="ws", question=f"q{i}", cypher=f"c{i}")
    out = store.search(workspace_id="ws", question="x", k=2)
    assert [ex.question for ex in out] == ["q2", "q1"]


def test_example_store_search_with_k_zero_returns_empty() -> None:
    from seocho.store.vector import NLCypherExampleStore

    store = NLCypherExampleStore()
    store.add(workspace_id="ws", question="q", cypher="c")
    assert store.search(workspace_id="ws", question="q", k=0) == []


# ---- Tool factories (with agents stub) -----------------------------------


def test_schema_introspect_tool_returns_graph_store_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agents(monkeypatch)
    tools = importlib.import_module("seocho.tools")

    class FakeStore:
        def get_schema(self, *, database: str, workspace_id: str):
            assert database == "neo4j"
            assert workspace_id == "ws-1"
            return {"labels": ["Entity"], "relationship_types": ["MENTIONS"], "property_keys": ["name"]}

    tool = tools.make_schema_introspect_tool(FakeStore(), workspace_id="ws-1")
    payload = json.loads(tool(database=""))
    assert payload == {
        "labels": ["Entity"],
        "relationship_types": ["MENTIONS"],
        "property_keys": ["name"],
    }


def test_schema_introspect_tool_swallows_store_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agents(monkeypatch)
    tools = importlib.import_module("seocho.tools")

    class BoomStore:
        def get_schema(self, *, database: str, workspace_id: str):
            raise RuntimeError("bolt down")

    tool = tools.make_schema_introspect_tool(BoomStore())
    payload = json.loads(tool(database=""))
    assert payload["labels"] == []
    assert "bolt down" in payload["error"]


def test_schema_with_stats_tool_combines_schema_and_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0097 G1: tool returns the union of get_schema + get_index_stats."""
    _stub_agents(monkeypatch)
    tools = importlib.import_module("seocho.tools")

    class FakeStore:
        def get_schema(self, *, database: str, workspace_id: str):
            assert database == "neo4j"
            assert workspace_id == "ws-1"
            return {
                "labels": ["Entity"],
                "relationship_types": ["MENTIONS"],
                "property_keys": ["name"],
            }

        def get_index_stats(self, *, database: str, workspace_id: str):
            assert database == "neo4j"
            assert workspace_id == "ws-1"
            return {
                "indexes": [{"name": "entity_name_idx", "type": "RANGE"}],
                "label_counts": {"Entity": 42},
                "rel_counts": {"MENTIONS": 7},
            }

    tool = tools.make_schema_with_stats_tool(FakeStore(), workspace_id="ws-1")
    payload = json.loads(tool(database=""))
    assert payload["labels"] == ["Entity"]
    assert payload["relationship_types"] == ["MENTIONS"]
    assert payload["indexes"][0]["name"] == "entity_name_idx"
    assert payload["label_counts"] == {"Entity": 42}
    assert payload["rel_counts"] == {"MENTIONS": 7}


def test_schema_with_stats_tool_swallows_store_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agents(monkeypatch)
    tools = importlib.import_module("seocho.tools")

    class BoomStore:
        def get_schema(self, *, database: str, workspace_id: str):
            raise RuntimeError("bolt down")

        def get_index_stats(self, *, database: str, workspace_id: str):
            raise RuntimeError("should not reach")

    tool = tools.make_schema_with_stats_tool(BoomStore())
    payload = json.loads(tool(database=""))
    assert payload["labels"] == []
    assert payload["indexes"] == []
    assert payload["label_counts"] == {}
    assert "bolt down" in payload["error"]


def test_validate_cypher_tool_rejects_forbidden_keywords(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agents(monkeypatch)
    tools = importlib.import_module("seocho.tools")

    tool = tools.make_validate_cypher_tool(workspace_id="ws-1")
    payload = json.loads(
        tool(
            cypher="MATCH (n {id:$node_id}) DETACH DELETE n RETURN n",
            params_json='{"node_id":"x"}',
        )
    )
    assert payload["ok"] is False
    assert any("forbidden_token" in v for v in payload["violations"])


def test_validate_cypher_tool_flags_missing_node_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agents(monkeypatch)
    tools = importlib.import_module("seocho.tools")

    tool = tools.make_validate_cypher_tool(workspace_id="ws-1")
    payload = json.loads(tool(cypher="MATCH (n) RETURN n", params_json="{}"))
    assert payload["ok"] is False
    assert "missing_node_binding" in payload["violations"]


def test_validate_cypher_tool_passes_clean_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agents(monkeypatch)
    tools = importlib.import_module("seocho.tools")

    tool = tools.make_validate_cypher_tool(workspace_id="ws-1")
    payload = json.loads(
        tool(
            cypher="MATCH (n:Entity {id:$node_id}) RETURN n",
            params_json='{"node_id":"x"}',
            allowed_labels_csv="Entity",
            allowed_properties_csv="id",
        )
    )
    assert payload["ok"] is True
    assert payload["labels"] == ["Entity"]
    assert payload["workspace_id"] == "ws-1"


def test_similar_query_search_tool_returns_empty_without_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agents(monkeypatch)
    tools = importlib.import_module("seocho.tools")

    tool = tools.make_similar_query_search_tool(None, workspace_id="ws-1")
    payload = json.loads(tool(question="anything", k=5))
    assert payload == {"examples": [], "count": 0}


def test_similar_query_search_tool_returns_store_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agents(monkeypatch)
    from seocho.store.vector import NLCypherExampleStore

    tools = importlib.import_module("seocho.tools")

    store = NLCypherExampleStore()
    store.add(workspace_id="ws-1", question="Find Apple", cypher="MATCH (a) RETURN a")

    tool = tools.make_similar_query_search_tool(store, workspace_id="ws-1")
    payload = json.loads(tool(question="Find Apple", k=5))
    assert payload["count"] == 1
    assert payload["examples"][0]["question"] == "Find Apple"


# ---- create_query_tools wiring -------------------------------------------


def test_create_query_tools_returns_tiered_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agents(monkeypatch)
    tools = importlib.import_module("seocho.tools")

    class FakeOntology:
        graph_model = "lpg"
        name = "test"
        namespace = "test"
        nodes: dict = {}
        relations: dict = {}
        property_types: dict = {}

        def to_query_context(self):
            return {"ontology_name": "test"}

    class FakeStore:
        def get_schema(self, *, database: str, workspace_id: str):
            return {"labels": [], "relationship_types": [], "property_keys": []}

        def query(self, *_a, **_kw):
            return []

    out = tools.create_query_tools(
        ontology=FakeOntology(),
        graph_store=FakeStore(),
        workspace_id="ws-1",
    )
    names = {getattr(t, "__name__", None) for t in out}
    assert "text2cypher" in names
    assert "execute_cypher" in names
    assert "schema_introspect" in names
    assert "validate_cypher" in names
    assert "similar_query_search" in names


# ---- factory.py system prompt covers tiered policy ------------------------


def test_query_system_prompt_documents_tiered_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agents(monkeypatch)
    if "seocho.agent.factory" in sys.modules:
        del sys.modules["seocho.agent.factory"]
    factory = importlib.import_module("seocho.agent.factory")

    class FakeOntology:
        def to_query_context(self):
            return {
                "ontology_name": "test",
                "graph_schema": "schema",
                "query_hints": "hints",
                "node_types": "Entity",
                "relationship_types": "MENTIONS",
            }

    prompt = factory.query_system_prompt(FakeOntology())
    assert "Tier 1" in prompt
    assert "Tier 2" in prompt
    assert "Tier 3" in prompt
    assert "schema_introspect" in prompt
    assert "validate_cypher" in prompt
    assert "similar_query_search" in prompt
