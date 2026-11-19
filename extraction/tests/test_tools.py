"""Exercise the registered tool entrypoints without a graph service."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from agents import RunContextWrapper
from agents.agent import Agent
from agents.tool import ToolContext
from agents.run_config import RunConfig

from runtime import agent_server, server_runtime


def test_runtime_helpers_have_one_owner() -> None:
    for name in ('get_databases_impl', 'get_graphs_impl', 'get_schema_impl'):
        assert getattr(agent_server, name) is getattr(server_runtime, name)


@pytest.mark.asyncio
async def test_schema_tool_uses_current_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = MagicMock()
    registry.is_valid.return_value = True
    connector = MagicMock()
    connector.get_schema.side_effect = ['Node: Person', 'Node: Person, Company']
    monkeypatch.setattr(server_runtime, 'db_registry', registry)
    monkeypatch.setattr(server_runtime, 'get_neo4j_connector_service', lambda: connector)

    agent = Agent(name="test_agent", instructions="test")
    context = ToolContext(agent=agent, run_config=RunConfig(), tool_name="test", context=RunContextWrapper(context=None), tool_arguments={}, tool_call_id="123")

    invoke = agent_server.get_schema_tool.on_invoke_tool
    assert await invoke(context, '{"database":"kgnormal"}') == 'Node: Person'
    assert await invoke(context, '{"database":"kgnormal"}') == 'Node: Person, Company'
    connector.get_schema.assert_called_with('kgnormal')


@pytest.mark.asyncio
async def test_unknown_database_does_not_open_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = MagicMock()
    registry.is_valid.return_value = False
    registry.list_databases.return_value = ['neo4j']
    factory = MagicMock()
    monkeypatch.setattr(server_runtime, 'db_registry', registry)
    monkeypatch.setattr(server_runtime, 'get_neo4j_connector_service', factory)

    agent = Agent(name="test_agent", instructions="test")
    context = ToolContext(agent=agent, run_config=RunConfig(), tool_name="test", context=RunContextWrapper(context=None), tool_arguments={}, tool_call_id="123")

    result = await agent_server.get_schema_tool.on_invoke_tool(
        context, '{"database":"unknown_db"}',
    )
    assert "Unknown database 'unknown_db'" in result
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_graph_and_database_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    databases = MagicMock()
    databases.list_databases.return_value = ['example_db']
    graphs = MagicMock()
    graphs.list_graph_ids.return_value = ['example_graph']
    descriptor = {'graph_id': 'example_graph', 'database': 'example_db', 'workspace_scope': 'example'}
    target = MagicMock()
    target.to_public_dict.return_value = descriptor
    graphs.list_graphs.return_value = [target]
    monkeypatch.setattr(server_runtime, 'db_registry', databases)
    monkeypatch.setattr(server_runtime, 'graph_registry', graphs)

    agent = Agent(name="test_agent", instructions="test")
    context = ToolContext(agent=agent, run_config=RunConfig(), tool_name="test", context=RunContextWrapper(context=None), tool_arguments={}, tool_call_id="123")

    result = await agent_server.get_graphs_tool.on_invoke_tool(context, '{}')
    assert json.loads(result) == [descriptor]
    result = await agent_server.get_databases_tool.on_invoke_tool(context, '{}')
    assert 'example_db' in result and 'example_graph' in result
