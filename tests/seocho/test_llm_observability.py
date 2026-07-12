from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from seocho.store.llm import create_llm_backend
from seocho.tracing import TracingBackend, disable_tracing, enable_tracing


class _FakeChatCompletions:
    def create(self, **kwargs: Any) -> Any:
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            model=kwargs["model"],
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=2,
                total_tokens=12,
                cached_tokens=8,
            ),
        )


class _FakeOpenAIClient:
    def __init__(self, **kwargs: Any) -> None:
        self.chat = SimpleNamespace(completions=_FakeChatCompletions())


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("openai")
    module.OpenAI = _FakeOpenAIClient
    module.AsyncOpenAI = _FakeOpenAIClient
    monkeypatch.setitem(sys.modules, "openai", module)


class _Recorder(TracingBackend):
    def __init__(self) -> None:
        self.spans: List[Dict[str, Any]] = []

    def log_span(
        self,
        name: str,
        *,
        input_data: Optional[Dict[str, Any]] = None,
        output_data: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        self.spans.append({
            "name": name,
            "input": input_data or {},
            "output": output_data or {},
            "metadata": metadata or {},
            "tags": tags or [],
        })


def test_mara_completion_span_is_content_free_by_default(
    fake_openai: None,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MARA_API_KEY", "secret")
    monkeypatch.delenv("SEOCHO_TRACE_CAPTURE_CONTENT", raising=False)
    recorder = _Recorder()
    try:
        enable_tracing(backend=recorder)
        result = create_llm_backend(provider="mara").complete(
            system="private policy prompt",
            user="wallet 0xsecret belongs to Alice",
            task_hint="withdrawal_explanation.v1",
        )
    finally:
        disable_tracing()

    assert result.text == "ok"
    span = next(item for item in recorder.spans if item["name"] == "gen_ai.chat")
    rendered = repr(span)
    assert span["metadata"]["gen_ai.provider.name"] == "mara"
    assert span["output"]["gen_ai.usage.total_tokens"] == 12
    assert span["output"]["gen_ai.usage.cached_input_tokens"] == 8
    assert span["output"]["seocho.llm.attempt_count"] == 1
    assert "0xsecret" not in rendered
    assert "Alice" not in rendered
    assert "private policy prompt" not in rendered


def test_completion_content_capture_is_explicit(fake_openai: None, monkeypatch) -> None:
    monkeypatch.setenv("MARA_API_KEY", "secret")
    monkeypatch.setenv("SEOCHO_TRACE_CAPTURE_CONTENT", "1")
    recorder = _Recorder()
    try:
        enable_tracing(backend=recorder)
        create_llm_backend(provider="mara").complete(system="system", user="user")
    finally:
        disable_tracing()

    span = next(item for item in recorder.spans if item["name"] == "gen_ai.chat")
    assert span["input"]["gen_ai.prompt.system"] == "system"
    assert span["input"]["gen_ai.prompt.user"] == "user"
    assert span["output"]["gen_ai.completion"] == "ok"
