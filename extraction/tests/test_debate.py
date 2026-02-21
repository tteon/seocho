"""Tests for debate orchestrator runtime contracts."""

import os
import sys
import types
from contextlib import nullcontext

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

fake_agents = types.SimpleNamespace(
    Agent=object,
    Runner=object,
    function_tool=lambda fn: fn,
    RunContextWrapper=object,
    trace=lambda *_a, **_k: nullcontext(),
)
sys.modules["agents"] = fake_agents

import debate


class _DummyAgent:
    def __init__(self, name: str):
        self.name = name


class _DummyMemory:
    def __init__(self):
        self._store = {}

    def put(self, key: str, value: str) -> None:
        self._store[key] = value


@pytest.mark.anyio
async def test_debate_orchestrator_uses_starting_agent(monkeypatch):
    class _Runner:
        @staticmethod
        async def run(*, starting_agent, input, context):
            return types.SimpleNamespace(final_output=f"{starting_agent.name}:{input}", chat_history=[])

    monkeypatch.setattr(debate, "Runner", _Runner)
    monkeypatch.setattr(debate, "trace", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(debate, "update_current_trace", lambda **_k: None)
    monkeypatch.setattr(debate, "update_current_span", lambda **_k: None)

    orchestrator = debate.DebateOrchestrator(
        agents={"kgnormal": _DummyAgent("Agent_kgnormal")},
        supervisor=_DummyAgent("Supervisor"),
        shared_memory=_DummyMemory(),
    )

    result = await orchestrator.run_debate("hello", context=types.SimpleNamespace())

    assert result["response"].startswith("Supervisor:Original Question: hello")
    assert "Agent_kgnormal:hello" in result["response"]
    assert result["debate_results"][0]["db"] == "kgnormal"
