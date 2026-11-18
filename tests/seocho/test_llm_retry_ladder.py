"""sr-ekq.11: the completion retry ladder owns every resend.

Counts real HTTP requests through the openai SDK (``httpx.MockTransport``),
so hidden SDK retries would show up as extra requests. No network access.
"""

from __future__ import annotations

import asyncio
import json
import sys
from types import ModuleType
from typing import Any, Dict, List

import httpx
import openai
import pytest

from seocho.exceptions import SeochoProviderTerminalError
from seocho.store import llm as llm_module
from seocho.store.llm import OpenAICompatibleBackend, create_llm_backend

_OK_BODY = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 0,
    "model": "deepseek-chat",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": '{"ok": true}'},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
}


def _error(status: int, message: str = "error", headers: Dict[str, str] | None = None):
    return httpx.Response(
        status,
        json={"error": {"message": message, "type": "error", "code": None}},
        headers=headers,
    )


def _ok() -> httpx.Response:
    return httpx.Response(200, json=_OK_BODY)


class _Recorder:
    """Scripted responder: ``script`` is consumed in order, then ``default``."""

    def __init__(self, script: List[Any], default: Any = None) -> None:
        self.script = list(script)
        self.default = default
        self.requests: List[Dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        step = self.script.pop(0) if self.script else self.default
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step()
        return step


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> List[float]:
    recorded: List[float] = []

    async def _async_sleep(delay: float) -> None:
        recorded.append(delay)

    monkeypatch.setattr(llm_module.time, "sleep", recorded.append)
    monkeypatch.setattr(llm_module.asyncio, "sleep", _async_sleep)
    return recorded


def _backend(recorder: _Recorder) -> OpenAICompatibleBackend:
    # deepseek + reasoning + response_format builds the full 4-variant ladder.
    backend = create_llm_backend(provider="deepseek", model="deepseek-chat", api_key="k")
    transport = httpx.MockTransport(recorder)

    async def _async_handler(request: httpx.Request) -> httpx.Response:
        return recorder(request)

    # with_options keeps the constructed client's max_retries, so a hidden
    # SDK retry would surface as an extra recorded request.
    backend._client = backend._client.with_options(http_client=httpx.Client(transport=transport))
    backend._async_client = backend._async_client.with_options(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_async_handler))
    )
    return backend


_CALL = dict(
    system="Return valid json.",
    user="ok",
    response_format={"type": "json_object"},
    reasoning_mode=True,
    task_hint="graph_cot",
)


def _complete(backend: OpenAICompatibleBackend, use_async: bool):
    if use_async:
        return asyncio.run(backend.acomplete(**_CALL))
    return backend.complete(**_CALL)


def test_clients_are_built_without_sdk_retries() -> None:
    backend = OpenAICompatibleBackend(provider="mara", model="MiniMax-M2.7", api_key="k")
    assert backend._client.max_retries == 0
    assert backend._async_client.max_retries == 0


def test_agents_sdk_client_keeps_sdk_default_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    chat_module = ModuleType("agents.models.openai_chatcompletions")

    class FakeAgentsModel:
        def __init__(self, *, model: str, openai_client: Any) -> None:
            self.model = model
            self.openai_client = openai_client

    chat_module.OpenAIChatCompletionsModel = FakeAgentsModel
    monkeypatch.setitem(sys.modules, "agents.models.openai_chatcompletions", chat_module)

    backend = OpenAICompatibleBackend(provider="mara", model="MiniMax-M2.7", api_key="k")
    sdk_model = backend.to_agents_sdk_model()
    assert sdk_model.openai_client.max_retries == openai.DEFAULT_MAX_RETRIES
    assert backend._async_client.max_retries == 0


@pytest.mark.parametrize("use_async", [False, True])
def test_variant_ladder_shape_is_four_requests_on_payload_400s(sleeps, use_async) -> None:
    recorder = _Recorder([], default=lambda: _error(400, "unsupported parameter"))
    backend = _backend(recorder)

    with pytest.raises(openai.BadRequestError):
        _complete(backend, use_async)

    assert len(recorder.requests) == 4
    assert "response_format" not in recorder.requests[-1]
    assert sleeps == []


@pytest.mark.parametrize("use_async", [False, True])
@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "provider.auth"), (402, "provider.payment_required"), (403, "provider.auth")],
)
def test_auth_and_payment_errors_are_terminal(sleeps, use_async, status, code) -> None:
    recorder = _Recorder([], default=lambda: _error(status, "nope"))
    backend = _backend(recorder)

    with pytest.raises(SeochoProviderTerminalError) as info:
        _complete(backend, use_async)

    assert len(recorder.requests) == 1
    assert sleeps == []
    err = info.value
    assert err.status_code == status
    assert err.code == code
    assert err.provider == "deepseek"
    assert err.model == "deepseek-chat"
    assert isinstance(err.__cause__, openai.APIStatusError)
    assert f"HTTP {status}" in str(err) and "not retrying" in str(err)


@pytest.mark.parametrize("use_async", [False, True])
def test_terminal_after_payload_400_stops_the_ladder(sleeps, use_async) -> None:
    recorder = _Recorder([_error(400, "unsupported parameter")], default=lambda: _error(402))
    backend = _backend(recorder)

    with pytest.raises(SeochoProviderTerminalError):
        _complete(backend, use_async)

    assert len(recorder.requests) == 2


@pytest.mark.parametrize("use_async", [False, True])
@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_persistent_transport_errors_retry_same_variant_twice(sleeps, use_async, status) -> None:
    recorder = _Recorder([], default=lambda: _error(status))
    backend = _backend(recorder)

    with pytest.raises(openai.APIStatusError):
        _complete(backend, use_async)

    # 1 initial + 2 transport retries; no SDK retries, no variant stripping.
    assert len(recorder.requests) == 3
    assert all(r == recorder.requests[0] for r in recorder.requests)
    assert sleeps == [1.0, 2.0]


@pytest.mark.parametrize("use_async", [False, True])
def test_transport_error_then_success(sleeps, use_async) -> None:
    recorder = _Recorder([_error(503)], default=_ok)
    backend = _backend(recorder)

    response = _complete(backend, use_async)

    assert response.json() == {"ok": True}
    assert len(recorder.requests) == 2
    assert recorder.requests[0] == recorder.requests[1]
    assert sleeps == [1.0]


@pytest.mark.parametrize("use_async", [False, True])
def test_connection_errors_are_transport_retryable(sleeps, use_async) -> None:
    def _raise() -> httpx.Response:
        raise httpx.ConnectError("boom")

    recorder = _Recorder([], default=_raise)
    backend = _backend(recorder)

    with pytest.raises(openai.APIConnectionError):
        _complete(backend, use_async)

    assert len(recorder.requests) == 3


def test_retry_after_header_sets_backoff(sleeps) -> None:
    recorder = _Recorder(
        [_error(429, headers={"retry-after": "0.25"}), _error(429, headers={"retry-after": "999"})],
        default=_ok,
    )
    backend = _backend(recorder)

    backend.complete(**_CALL)

    assert len(recorder.requests) == 3
    assert sleeps == [0.25, llm_module.OpenAICompatibleBackend._TRANSPORT_RETRY_MAX_DELAY_S]


def test_transport_budget_is_per_logical_call_across_variants(sleeps) -> None:
    recorder = _Recorder(
        [_error(429), _error(400, "unsupported parameter"), _error(429)],
        default=lambda: _error(429),
    )
    backend = _backend(recorder)

    with pytest.raises(openai.RateLimitError):
        backend.complete(**_CALL)

    # 429 (retry 1) -> 400 (next variant) -> 429 (retry 2) -> 429 (budget out)
    assert len(recorder.requests) == 4
    assert sleeps == [1.0, 2.0]


def test_json_truncation_400_boost_still_precedes_strip(sleeps) -> None:
    recorder = _Recorder(
        [_error(400, "Model did not output valid JSON. The output was truncated before a complete JSON object")],
        default=_ok,
    )
    backend = _backend(recorder)

    backend.complete(**{**_CALL, "max_tokens": 8192})

    assert len(recorder.requests) == 2
    assert recorder.requests[1]["response_format"] == {"type": "json_object"}
    assert recorder.requests[1]["max_tokens"] == 16384
    assert sleeps == []

