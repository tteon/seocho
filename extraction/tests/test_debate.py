"""Tests for debate orchestrator runtime contracts."""

import os
import sys
import types
from contextlib import contextmanager, nullcontext

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeAgent:
    def __init__(self, *args, **kwargs):
        self.name = kwargs.get("name", "FakeAgent")
        self.instructions = kwargs.get("instructions", "")
        self.tools = kwargs.get("tools", [])
        self.handoffs = kwargs.get("handoffs", [])


fake_agents = types.SimpleNamespace(
    Agent=_FakeAgent,
    Runner=object,
    function_tool=lambda fn: fn,
    RunContextWrapper=object,
    trace=lambda *_a, **_k: nullcontext(),
)
sys.modules["agents"] = fake_agents

import debate


class _DummyAgent:
    def __init__(self, name: str, graph_database: str | None = None):
        self.name = name
        self.graph_database = graph_database or name


class _DummyMemory:
    def __init__(self):
        self._store = {}

    def put(self, key: str, value: str) -> None:
        self._store[key] = value


def test_debate_no_data_detector_handles_agent_phrase_variants():
    assert debate.DebateOrchestrator._should_fallback_to_semantic(
        "The graph does not provide information about PTC revenue growth."
    )
    assert debate.DebateOrchestrator._should_fallback_to_semantic(
        "Based on the current data, there is no available information."
    )
    assert debate.DebateOrchestrator._should_fallback_to_semantic(
        "There are no business segments for Brown & Brown found in the current graph database."
    )
    assert debate.DebateOrchestrator._should_fallback_to_semantic(
        "There is currently no information about key executives at Alphabet in the available graph database."
    )
    assert debate.DebateOrchestrator._should_fallback_to_semantic(
        "There is no specific information recorded about what drove NVIDIA's gross margin expansion."
    )
    assert debate.DebateOrchestrator._should_fallback_to_semantic(
        "There are no factors linked via the DRIVEN_BY relationship."
    )
    assert debate.DebateOrchestrator._should_fallback_to_semantic(
        "There are no recorded business segments for Brown & Brown."
    )
    assert debate.DebateOrchestrator._should_fallback_to_semantic(
        "The system does not show any information or entries linked to Brown & Brown's business segments."
    )
    assert debate.DebateOrchestrator._should_fallback_to_semantic(
        "The sources do not explicitly record any legal issues Microsoft is facing."
    )
    assert debate.DebateOrchestrator._should_fallback_to_semantic(
        "There are no records of key executives for Alphabet in the current scope."
    )
    assert debate.DebateOrchestrator._should_fallback_to_semantic(
        "There is currently no information provided about the key executives at Alphabet."
    )
    assert debate.DebateOrchestrator._should_fallback_to_semantic(
        "There is no detailed information regarding the specific factors that drove NVIDIA's gross margin expansion."
    )
    assert debate.DebateOrchestrator._should_fallback_to_semantic(
        "A search for relevant entities did not yield any results."
    )
    assert debate.DebateOrchestrator._should_fallback_to_semantic(
        "There are no business segments for Brown & Brown listed in the graph database."
    )
    assert debate.DebateOrchestrator._should_fallback_to_semantic(
        "I could not retrieve any data from the graph about Tesla deliveries."
    )
    assert debate.DebateOrchestrator._should_fallback_to_semantic(
        'I do not see any nodes or data directly related to "NVIDIA".'
    )
    assert debate.DebateOrchestrator._should_fallback_to_semantic(
        "I am unable to answer what drove NVIDIA's gross margin expansion."
    )
    assert debate.DebateOrchestrator._should_fallback_to_semantic(
        "NVIDIA and related financial details are outside the current scope."
    )


@pytest.mark.anyio
async def test_debate_orchestrator_uses_starting_agent(monkeypatch):
    class _Runtime:
        @staticmethod
        async def run(*, agent, input, context):
            return types.SimpleNamespace(final_output=f"{agent.name}:{input}", chat_history=[])

        @staticmethod
        @contextmanager
        def trace(name: str):
            yield

    monkeypatch.setattr(debate, "update_current_trace", lambda **_k: None)
    monkeypatch.setattr(debate, "update_current_span", lambda **_k: None)

    orchestrator = debate.DebateOrchestrator(
        agents={"graph-normal": _DummyAgent("Agent_kgnormal", graph_database="kgnormal")},
        supervisor=_DummyAgent("Supervisor"),
        shared_memory=_DummyMemory(),
        agents_runtime=_Runtime(),
    )

    result = await orchestrator.run_debate("hello", context=types.SimpleNamespace())

    assert result["response"].startswith("Supervisor:Original Question: hello")
    assert "Agent_kgnormal:hello" in result["response"]
    assert result["debate_results"][0]["db"] == "kgnormal"
    assert result["debate_results"][0]["graph"] == "graph-normal"


@pytest.mark.anyio
async def test_debate_orchestrator_uses_semantic_preflight_when_supported(monkeypatch):
    calls = []

    class _Runtime:
        @staticmethod
        async def run(*, agent, input, context):
            calls.append(agent.name)
            if agent.name == "Supervisor":
                return types.SimpleNamespace(final_output=f"Supervisor:{input}", chat_history=[])
            raise AssertionError("graph agent should be skipped when semantic preflight is supported")

        @staticmethod
        @contextmanager
        def trace(name: str):
            yield

    class _SemanticFlow:
        def run(self, **_kwargs):
            return {
                "response": "Deterministic graph answer.",
                "trace_steps": [],
                "support_assessment": {"status": "supported", "supported": True},
                "lpg_result": {"records": [{"company": "ACME"}]},
                "rdf_result": None,
            }

    monkeypatch.setattr(debate, "update_current_trace", lambda **_k: None)
    monkeypatch.setattr(debate, "update_current_span", lambda **_k: None)

    orchestrator = debate.DebateOrchestrator(
        agents={"graph-normal": _DummyAgent("Agent_kgnormal", graph_database="kgnormal")},
        supervisor=_DummyAgent("Supervisor"),
        shared_memory=_DummyMemory(),
        agents_runtime=_Runtime(),
    )

    context = types.SimpleNamespace(
        workspace_id="default",
        semantic_agent_flow=_SemanticFlow(),
    )
    result = await orchestrator.run_debate("What was PTC revenue growth?", context=context)

    assert result["debate_results"][0]["response"] == "Deterministic graph answer."
    assert "Deterministic graph answer." in result["response"]
    assert calls == ["Supervisor"]
    assert any(step["type"] == "DETERMINISTIC_PREFLIGHT" for step in result["trace_steps"])


@pytest.mark.anyio
async def test_debate_orchestrator_falls_back_to_semantic_flow_on_no_data(monkeypatch):
    semantic_calls = 0

    class _Runtime:
        @staticmethod
        async def run(*, agent, input, context):
            if agent.name == "Supervisor":
                return types.SimpleNamespace(final_output=f"Supervisor:{input}", chat_history=[])
            return types.SimpleNamespace(final_output="I could not find any data.", chat_history=[])

        @staticmethod
        @contextmanager
        def trace(name: str):
            yield

    class _SemanticFlow:
        def run(self, *, question, databases, entity_overrides, workspace_id, reasoning_mode, repair_budget):
            nonlocal semantic_calls
            semantic_calls += 1
            assert question == "What was PTC revenue growth?"
            assert databases == ["kgnormal"]
            assert entity_overrides == {}
            assert workspace_id == "default"
            assert reasoning_mode is False
            assert repair_budget == 0
            if semantic_calls == 1:
                return {
                    "response": "Ungrounded semantic answer.",
                    "trace_steps": [],
                    "support_assessment": {"status": "unsupported", "supported": False},
                    "lpg_result": {"records": []},
                    "rdf_result": None,
                }
            return {
                "response": "PTC reported revenue growth from graph evidence.",
                "trace_steps": [],
                "support_assessment": {"status": "supported", "supported": True},
                "lpg_result": {"records": [{"company": "PTC"}]},
                "rdf_result": None,
            }

    monkeypatch.setattr(debate, "update_current_trace", lambda **_k: None)
    monkeypatch.setattr(debate, "update_current_span", lambda **_k: None)

    orchestrator = debate.DebateOrchestrator(
        agents={"graph-normal": _DummyAgent("Agent_kgnormal", graph_database="kgnormal")},
        supervisor=_DummyAgent("Supervisor"),
        shared_memory=_DummyMemory(),
        agents_runtime=_Runtime(),
    )

    context = types.SimpleNamespace(
        workspace_id="default",
        semantic_agent_flow=_SemanticFlow(),
    )
    result = await orchestrator.run_debate("What was PTC revenue growth?", context=context)

    assert result["debate_results"][0]["response"] == "PTC reported revenue growth from graph evidence."
    assert "PTC reported revenue growth from graph evidence." in result["response"]
    assert semantic_calls == 2
    assert any(step["type"] == "DETERMINISTIC_FALLBACK" for step in result["trace_steps"])


@pytest.mark.anyio
async def test_debate_orchestrator_runs_graph_agent_when_preflight_is_unsupported(monkeypatch):
    class _Runtime:
        @staticmethod
        async def run(*, agent, input, context):
            if agent.name == "Supervisor":
                return types.SimpleNamespace(final_output=f"Supervisor:{input}", chat_history=[])
            return types.SimpleNamespace(final_output="Graph agent answer.", chat_history=[])

        @staticmethod
        @contextmanager
        def trace(name: str):
            yield

    class _SemanticFlow:
        def run(self, **_kwargs):
            return {
                "response": "Ungrounded semantic answer.",
                "trace_steps": [],
                "support_assessment": {"status": "unsupported", "supported": False},
                "lpg_result": {"records": []},
                "rdf_result": None,
            }

    monkeypatch.setattr(debate, "update_current_trace", lambda **_k: None)
    monkeypatch.setattr(debate, "update_current_span", lambda **_k: None)

    orchestrator = debate.DebateOrchestrator(
        agents={"graph-normal": _DummyAgent("Agent_kgnormal", graph_database="kgnormal")},
        supervisor=_DummyAgent("Supervisor"),
        shared_memory=_DummyMemory(),
        agents_runtime=_Runtime(),
    )

    context = types.SimpleNamespace(
        workspace_id="default",
        semantic_agent_flow=_SemanticFlow(),
    )
    result = await orchestrator.run_debate("What was PTC revenue growth?", context=context)

    assert result["debate_results"][0]["response"] == "Graph agent answer."
    assert not any(step["type"] == "DETERMINISTIC_PREFLIGHT" for step in result["trace_steps"])


@pytest.mark.anyio
async def test_debate_orchestrator_falls_back_to_semantic_flow_on_agent_error(monkeypatch):
    semantic_calls = 0

    class _Runtime:
        @staticmethod
        async def run(*, agent, input, context):
            if agent.name == "Supervisor":
                return types.SimpleNamespace(final_output=f"Supervisor:{input}", chat_history=[])
            raise RuntimeError("tool failure")

        @staticmethod
        @contextmanager
        def trace(name: str):
            yield

    class _SemanticFlow:
        def run(self, **_kwargs):
            nonlocal semantic_calls
            semantic_calls += 1
            if semantic_calls == 1:
                return {
                    "response": "Ungrounded semantic answer.",
                    "trace_steps": [],
                    "support_assessment": {"status": "unsupported", "supported": False},
                    "lpg_result": {"records": []},
                    "rdf_result": None,
                }
            return {
                "response": "Recovered deterministic graph evidence.",
                "trace_steps": [],
                "support_assessment": {"status": "supported", "supported": True},
                "lpg_result": {"records": [{"company": "PTC"}]},
                "rdf_result": None,
            }

    monkeypatch.setattr(debate, "update_current_trace", lambda **_k: None)
    monkeypatch.setattr(debate, "update_current_span", lambda **_k: None)

    orchestrator = debate.DebateOrchestrator(
        agents={"graph-normal": _DummyAgent("Agent_kgnormal", graph_database="kgnormal")},
        supervisor=_DummyAgent("Supervisor"),
        shared_memory=_DummyMemory(),
        agents_runtime=_Runtime(),
    )

    context = types.SimpleNamespace(
        workspace_id="default",
        semantic_agent_flow=_SemanticFlow(),
    )
    result = await orchestrator.run_debate("What was PTC revenue growth?", context=context)

    assert result["debate_results"][0]["response"] == "Recovered deterministic graph evidence."
    assert "Recovered deterministic graph evidence." in result["response"]
    assert semantic_calls == 2
    debate_step = next(step for step in result["trace_steps"] if step["type"] == "DETERMINISTIC_FALLBACK")
    assert debate_step["metadata"]["fallback_reason"] == "agent_error"
