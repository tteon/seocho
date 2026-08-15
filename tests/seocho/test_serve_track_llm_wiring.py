"""The LLM call path feeds WindowRecorder, and the window carries the stage name.

This is the seam the serve-track KV rig depends on: vLLM's cache events name no
request, so a block is attributable only if something recorded *when* each call
ran and *which* stage issued it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

from seocho.observability import (  # noqa: E402
    StageTimer,
    current_stage,
    observe_llm_call,
    set_llm_call_observer,
)
from seocho.store.llm import OpenAICompatibleBackend  # noqa: E402


def _load_kv_windows():
    spec = importlib.util.spec_from_file_location(
        "serve_track_kv_windows", _ROOT / "scripts" / "serve_track" / "kv_windows.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


kv_windows = _load_kv_windows()


@pytest.fixture(autouse=True)
def _clear_observer():
    yield
    set_llm_call_observer(None)


class _StubCompletions:
    def __init__(self, usage):
        self._usage = usage
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok", model_extra={}))],
            usage=self._usage,
            model="stub-model",
        )


def _backend(usage) -> OpenAICompatibleBackend:
    backend = OpenAICompatibleBackend(provider="openai", model="stub-model", api_key="k")
    backend._client = SimpleNamespace(chat=SimpleNamespace(completions=_StubCompletions(usage)))
    return backend


def _usage(prompt=900, cached=None):
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=10,
        total_tokens=prompt + 10,
        prompt_tokens_details=({"cached_tokens": cached} if cached is not None else None),
    )


def test_stage_name_reaches_the_window(tmp_path):
    """A call inside StageTimer.stage('generation') must be attributed to it."""
    recorder = kv_windows.WindowRecorder(tmp_path / "kv_windows.jsonl")
    set_llm_call_observer(
        lambda **kw: recorder.record_step(trace_id="run-1", **kw)
    )

    timer = StageTimer()
    backend = _backend(_usage(prompt=900, cached=880))
    with timer.stage("generation"):
        backend.complete(system="S" * 400, user="U" * 100)

    records = kv_windows.read_windows(tmp_path / "kv_windows.jsonl")
    assert len(records) == 1
    window = records[0]
    assert window["role"] == "generation"
    assert window["provider"] == "openai"
    assert window["t_end"] > window["t_start"]
    # system before user is the layout that decides prefix reuse.
    assert window["prompt_sections"] == {"system": 400, "user": 100}
    assert window["prompt_chars"] == 500
    assert window["usage"]["prompt_tokens"] == 900
    assert window["usage"]["cached_tokens"] == 880


def test_no_observer_installed_is_a_no_op(tmp_path):
    """Nothing observes unless something explicitly asked to."""
    backend = _backend(_usage())
    with StageTimer().stage("generation"):
        result = backend.complete(system="S", user="U")
    assert result.text == "ok"
    assert not (tmp_path / "kv_windows.jsonl").exists()


def test_observer_failure_never_breaks_the_call():
    """Instrumentation must not be able to fail a query."""

    def _broken(**_kwargs):
        raise RuntimeError("recorder exploded")

    set_llm_call_observer(_broken)
    backend = _backend(_usage())
    assert backend.complete(system="S", user="U").text == "ok"


def test_failed_call_still_closes_its_window(tmp_path):
    """A call that failed still consumed cache; its window must be recorded."""
    recorder = kv_windows.WindowRecorder(tmp_path / "kv_windows.jsonl")
    set_llm_call_observer(lambda **kw: recorder.record_step(trace_id="run-1", **kw))

    with pytest.raises(RuntimeError):
        with observe_llm_call(role="synthesize", model="m", provider="vllm"):
            raise RuntimeError("upstream 500")

    records = kv_windows.read_windows(tmp_path / "kv_windows.jsonl")
    assert len(records) == 1
    assert records[0]["role"] == "synthesize"


def test_stage_var_is_restored_after_the_stage():
    timer = StageTimer()
    assert current_stage() == ""
    with timer.stage("plan"):
        assert current_stage() == "plan"
        with timer.stage("repair"):
            assert current_stage() == "repair"
        assert current_stage() == "plan"
    assert current_stage() == ""
