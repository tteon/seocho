from __future__ import annotations

from types import SimpleNamespace

from examples.finder.lib import llm_io


def _response(content: str = "answer") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(
            prompt_tokens=11,
            completion_tokens=7,
            total_tokens=18,
        ),
        _hidden_params={"response_cost": 0.0125},
    )


def test_litellm_client_routes_mara_through_openai_compatible_prefix(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MARA_API_KEY", "test-key")
    calls: list[dict] = []

    def fake_completion(**kwargs):
        calls.append(kwargs)
        return _response()

    spec = llm_io.parse_llm_spec("mara/MiniMax-M2.7")
    client = llm_io.LiteLLMChatClient(
        spec=spec,
        timeout_s=45.0,
        completion_fn=fake_completion,
    )

    response = client.chat.completions.create(
        model=spec.model,
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.0,
    )

    assert response.choices[0].message.content == "answer"
    assert calls == [
        {
            "model": "openai/MiniMax-M2.7",
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.0,
            "api_key": "test-key",
            "timeout": 45.0,
            "max_retries": 0,
            "api_base": "https://api.cloud.mara.com/v1",
        }
    ]


def test_chat_complete_emits_portable_litellm_usage_receipt(monkeypatch) -> None:
    monkeypatch.setenv("MARA_API_KEY", "test-key")
    receipts: list[llm_io.LLMCallReceipt] = []
    spec = llm_io.parse_llm_spec("mara/MiniMax-M2.7")
    client = llm_io.LiteLLMChatClient(
        spec=spec,
        timeout_s=45.0,
        completion_fn=lambda **_: _response("grounded answer"),
    )

    text = llm_io.chat_complete(
        client=client,
        model=spec.model,
        system="system",
        user="question",
        temperature=0.0,
        label="agent-risk",
        spec=spec,
        receipt_sink=receipts.append,
    )

    assert text == "grounded answer"
    assert len(receipts) == 1
    assert receipts[0].transport == "litellm"
    assert receipts[0].provider == "mara"
    assert receipts[0].model == "MiniMax-M2.7"
    assert receipts[0].label == "agent-risk"
    assert receipts[0].prompt_tokens == 11
    assert receipts[0].completion_tokens == 7
    assert receipts[0].total_tokens == 18
    assert receipts[0].response_cost == 0.0125


def test_make_chat_client_can_select_litellm_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("SEOCHO_BENCH_LLM_TRANSPORT", "litellm")
    spec = llm_io.parse_llm_spec("mara/MiniMax-M2.7")

    client = llm_io.make_chat_client(spec)

    assert isinstance(client, llm_io.LiteLLMChatClient)
