"""Lightweight API integration checks."""

import importlib
import os
import sys
import types
from contextlib import nullcontext
from unittest.mock import MagicMock

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(scope="module")
def app_module():
    mock_graph_db = MagicMock()
    mock_graph_db.driver.return_value = MagicMock()
    fake_neo4j = types.ModuleType("neo4j")
    fake_neo4j.GraphDatabase = mock_graph_db
    fake_neo4j_exceptions = types.ModuleType("neo4j.exceptions")
    fake_neo4j_exceptions.ServiceUnavailable = RuntimeError
    fake_neo4j_exceptions.SessionExpired = RuntimeError
    fake_faiss = MagicMock()
    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = MagicMock()
    
    class DummyAgent:
        def __init__(self, *args, **kwargs):
            self.name = kwargs.get("name", "DummyAgent")
            self.instructions = kwargs.get("instructions", "")
            self.tools = kwargs.get("tools", [])
            self.handoffs = kwargs.get("handoffs", [])

    class DummyRunner:
        @staticmethod
        async def run(*args, **kwargs):
            return types.SimpleNamespace(final_output="", to_input_list=lambda: [])

    def function_tool(func):
        return func

    class DummyRunContextWrapper:
        pass

    fake_agents = types.SimpleNamespace(
        Agent=DummyAgent,
        Runner=DummyRunner,
        function_tool=function_tool,
        RunContextWrapper=DummyRunContextWrapper,
        trace=lambda *args, **kwargs: nullcontext(),
    )

    with pytest.MonkeyPatch().context() as mp:
        mp.setenv("OPENAI_API_KEY", "test-key")
        mp.setenv("OPIK_URL_OVERRIDE", "")
        with pytest.MonkeyPatch().context() as mp_modules:
            mp_modules.setitem(sys.modules, "neo4j", fake_neo4j)
            mp_modules.setitem(sys.modules, "neo4j.exceptions", fake_neo4j_exceptions)
            mp_modules.setitem(sys.modules, "faiss", fake_faiss)
            mp_modules.setitem(sys.modules, "openai", fake_openai)
            mp_modules.setitem(sys.modules, "agents", fake_agents)
            import agent_server

            return importlib.reload(agent_server)


@pytest.fixture
async def client(app_module):
    transport = httpx.ASGITransport(app=app_module.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


@pytest.mark.anyio
async def test_run_agent_health(client):
    response = await client.get("/run_agent")
    assert response.status_code == 405
