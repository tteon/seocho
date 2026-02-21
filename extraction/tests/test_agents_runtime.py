"""Contract tests for OpenAI Agents SDK adapter compatibility."""

import os
import sys
import types
from contextlib import nullcontext

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

fake_agents = types.SimpleNamespace(
    Runner=object,
    trace=lambda *_a, **_k: nullcontext(),
)
sys.modules["agents"] = fake_agents

import agents_runtime


@pytest.mark.anyio
async def test_adapter_uses_starting_agent_signature():
    class _Runner:
        @staticmethod
        async def run(*, starting_agent, input, context):
            return {"agent": starting_agent, "input": input, "context": context}

    runtime = agents_runtime.AgentsRuntimeAdapter(runner_cls=_Runner, trace_fn=lambda *_a, **_k: nullcontext())
    result = await runtime.run(agent="a1", input="hello", context={"k": "v"})

    assert result["agent"] == "a1"
    assert result["input"] == "hello"


@pytest.mark.anyio
async def test_adapter_uses_agent_signature():
    class _Runner:
        @staticmethod
        async def run(*, agent, input, context):
            return {"agent": agent, "input": input, "context": context}

    runtime = agents_runtime.AgentsRuntimeAdapter(runner_cls=_Runner, trace_fn=lambda *_a, **_k: nullcontext())
    result = await runtime.run(agent="a2", input="world", context={})

    assert result["agent"] == "a2"
    assert result["input"] == "world"


@pytest.mark.anyio
async def test_adapter_propagates_non_signature_typeerror():
    class _Runner:
        @staticmethod
        async def run(*, starting_agent, input, context):
            raise TypeError("boom")

    runtime = agents_runtime.AgentsRuntimeAdapter(runner_cls=_Runner, trace_fn=lambda *_a, **_k: nullcontext())

    with pytest.raises(TypeError, match="boom"):
        await runtime.run(agent="a3", input="x", context={})
