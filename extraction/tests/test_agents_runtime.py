"""Contract tests for OpenAI Agents SDK adapter compatibility.

Test isolation note. The adapter under test imports ``from agents import
Runner, trace`` at module load. To exercise it without the real OpenAI
Agents SDK installed, we install a stub at ``sys.modules["agents"]``
*before* the adapter import below. That mutation must be reversed when
this module's tests finish so subsequent test files in the same pytest
process see the production import graph again — otherwise any code path
that calls ``from agents import Agent`` resolves to the stub's
``Agent = object`` and downstream imports break with subtle errors
(e.g. ``TypeError: object() takes no arguments``).

``teardown_module`` below restores the original (or absence) and drops
the cached ``agents_runtime`` so a later import re-resolves cleanly
against the real ``agents``. Tracked in seocho-eug0.
"""

import os
import re
import sys
import types
from contextlib import nullcontext
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Snapshot must happen BEFORE the sys.modules mutation below so we have a
# handle on the original (or its absence) for teardown_module to restore.
_ORIGINAL_AGENTS_MODULE = sys.modules.get("agents")

fake_agents = types.SimpleNamespace(
    Runner=object,
    trace=lambda *_a, **_k: nullcontext(),
)
sys.modules["agents"] = fake_agents

import agents_runtime


def teardown_module(module):
    """Restore production sys.modules state after this file's tests run.

    Without this, ``sys.modules["agents"]`` keeps pointing at the stub
    namespace for the rest of the pytest process and poisons every
    subsequent test that imports ``from agents import ...`` —
    documented in seocho-eug0 as the cause of the cross-suite flake on
    seocho/tests/test_session_agent.py and the alias_flat_import test.
    """

    if _ORIGINAL_AGENTS_MODULE is None:
        sys.modules.pop("agents", None)
    else:
        sys.modules["agents"] = _ORIGINAL_AGENTS_MODULE

    # Drop the cached agents_runtime that was loaded against the stub.
    # Both names may exist depending on how a later import resolves.
    for cached in ("agents_runtime", "extraction.agents_runtime"):
        sys.modules.pop(cached, None)


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


@pytest.mark.anyio
async def test_adapter_run_omits_context_when_none():
    captured = {}

    class _Runner:
        @staticmethod
        async def run(*, starting_agent, input):
            captured["starting_agent"] = starting_agent
            captured["input"] = input
            return "ok"

    runtime = agents_runtime.AgentsRuntimeAdapter(
        runner_cls=_Runner, trace_fn=lambda *_a, **_k: nullcontext()
    )
    result = await runtime.run(agent="a", input="x")

    assert result == "ok"
    assert captured == {"starting_agent": "a", "input": "x"}


def test_adapter_run_streamed_uses_starting_agent_signature():
    captured = {}

    class _Streaming:
        def stream_events(self):
            return iter([])

    class _Runner:
        @staticmethod
        def run_streamed(*, starting_agent, input):
            captured["starting_agent"] = starting_agent
            captured["input"] = input
            return _Streaming()

    runtime = agents_runtime.AgentsRuntimeAdapter(
        runner_cls=_Runner, trace_fn=lambda *_a, **_k: nullcontext()
    )
    result = runtime.run_streamed(agent="b", input="y")

    assert isinstance(result, _Streaming)
    assert captured == {"starting_agent": "b", "input": "y"}


def test_adapter_run_streamed_falls_back_to_agent_signature():
    captured = {}

    class _Streaming:
        def stream_events(self):
            return iter([])

    class _Runner:
        @staticmethod
        def run_streamed(*, agent, input):
            captured["agent"] = agent
            captured["input"] = input
            return _Streaming()

    runtime = agents_runtime.AgentsRuntimeAdapter(
        runner_cls=_Runner, trace_fn=lambda *_a, **_k: nullcontext()
    )
    result = runtime.run_streamed(agent="c", input="z")

    assert isinstance(result, _Streaming)
    assert captured == {"agent": "c", "input": "z"}


def test_adapter_run_streamed_raises_when_unavailable():
    class _Runner:
        pass

    runtime = agents_runtime.AgentsRuntimeAdapter(
        runner_cls=_Runner, trace_fn=lambda *_a, **_k: nullcontext()
    )

    with pytest.raises(RuntimeError, match="run_streamed is unavailable"):
        runtime.run_streamed(agent="d", input="w")


def test_no_direct_runner_usage_outside_adapter():
    """Regression guard: only the adapter may call Runner.run / Runner.run_streamed.

    Routing all SDK calls through ``extraction/agents_runtime.py`` is a
    structural property required by CLAUDE.md §15. New direct ``Runner.``
    references in ``seocho/``, ``runtime/``, or ``extraction/`` (other than
    the adapter itself) silently re-introduce SDK signature drift risk.
    """
    repo_root = Path(__file__).resolve().parents[2]
    pattern = re.compile(r"\bRunner\.(?:run|run_streamed)\b")
    offenders: list[str] = []

    for package in ("seocho", "runtime", "extraction"):
        for py_file in (repo_root / package).rglob("*.py"):
            if py_file.name == "agents_runtime.py":
                continue
            if "tests" in py_file.parts:
                continue
            try:
                source = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if pattern.search(source):
                offenders.append(str(py_file.relative_to(repo_root)))

    assert offenders == [], (
        "Direct Runner.run / Runner.run_streamed calls found outside the adapter. "
        "Route through extraction.agents_runtime.get_agents_runtime() instead. "
        f"Offenders: {offenders}"
    )


@pytest.mark.anyio
async def test_adapter_passes_max_turns_when_runner_supports_it():
    class _Runner:
        @staticmethod
        async def run(*, starting_agent, input, context, max_turns):
            return {
                "agent": starting_agent,
                "input": input,
                "context": context,
                "max_turns": max_turns,
            }

    runtime = agents_runtime.AgentsRuntimeAdapter(runner_cls=_Runner, trace_fn=lambda *_a, **_k: nullcontext())
    result = await runtime.run(agent="a4", input="bounded", context={}, max_turns=5)

    assert result["agent"] == "a4"
    assert result["max_turns"] == 5
