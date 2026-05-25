from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from seocho.store.llm import (
    KimiBackend,
    VLLMBackend,
    create_embedding_backend,
    create_llm_backend,
)
from seocho.tracing import disable_tracing


class _FakeChatCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            model=kwargs["model"],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3),
        )


class _FakeEmbeddings:
    def create(self, *, model, input):
        return SimpleNamespace(
            data=[
                SimpleNamespace(embedding=[float(index + 1), 0.0])
                for index, _ in enumerate(input)
            ]
        )


class _FakeOpenAIClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.chat = SimpleNamespace(completions=_FakeChatCompletions())
        self.embeddings = _FakeEmbeddings()


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("openai")
    module.OpenAI = _FakeOpenAIClient
    module.AsyncOpenAI = _FakeOpenAIClient
    monkeypatch.setitem(sys.modules, "openai", module)


@pytest.fixture(autouse=True)
def reset_tracing_state() -> None:
    disable_tracing()
    yield
    disable_tracing()


@pytest.mark.parametrize(
    ("provider", "env_name", "base_url", "model"),
    [
        ("deepseek", "DEEPSEEK_API_KEY", "https://api.deepseek.com", "deepseek-chat"),
        ("kimi", "MOONSHOT_API_KEY", "https://api.moonshot.ai/v1", "kimi-k2.5"),
        ("grok", "XAI_API_KEY", "https://api.x.ai/v1", "grok-4.20-reasoning"),
        (
            "qwen",
            "DASHSCOPE_API_KEY",
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "qwen-plus",
        ),
    ],
)
def test_provider_presets_resolve_openai_compatible_defaults(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    env_name: str,
    base_url: str,
    model: str,
) -> None:
    monkeypatch.setenv(env_name, "secret-token")

    backend = create_llm_backend(provider=provider)

    assert backend.provider == provider
    assert backend.model == model
    assert backend._base_url == base_url
    assert backend._api_key_env == env_name


def test_grok_provider_accepts_legacy_grok_api_key_alias(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("GROK_API_KEY", "grok-secret")

    backend = create_llm_backend(provider="grok")

    assert backend.provider == "grok"
    assert backend._api_key == "grok-secret"
    assert backend._api_key_env == "XAI_API_KEY"


def test_openai_embedding_backend_uses_default_embedding_model(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")

    backend = create_embedding_backend(provider="openai")
    vectors = backend.embed(["alpha", "beta"])

    assert backend.model == "text-embedding-3-small"
    assert vectors == [[1.0, 0.0], [2.0, 0.0]]


def test_non_embedding_provider_requires_explicit_embedding_model(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")

    with pytest.raises(ValueError, match="default embedding model"):
        create_embedding_backend(provider="deepseek")


def test_agents_sdk_helpers_build_model_provider_and_run_config(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents_module = ModuleType("agents")

    class FakeRunConfig:
        def __init__(self, *, model):
            self.model = model

    agents_module.RunConfig = FakeRunConfig
    monkeypatch.setitem(sys.modules, "agents", agents_module)

    chat_module = ModuleType("agents.models.openai_chatcompletions")

    class FakeAgentsModel:
        def __init__(self, *, model, openai_client):
            self.model = model
            self.openai_client = openai_client

    chat_module.OpenAIChatCompletionsModel = FakeAgentsModel
    monkeypatch.setitem(sys.modules, "agents.models.openai_chatcompletions", chat_module)

    provider_module = ModuleType("agents.models.openai_provider")

    class FakeProvider:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    provider_module.OpenAIProvider = FakeProvider
    monkeypatch.setitem(sys.modules, "agents.models.openai_provider", provider_module)

    monkeypatch.setenv("MOONSHOT_API_KEY", "kimi-secret")
    backend = KimiBackend()

    sdk_model = backend.to_agents_sdk_model()
    sdk_provider = backend.to_agents_provider(use_responses=False)
    run_config = backend.to_agents_run_config()

    assert sdk_model.model == "kimi-k2.5"
    assert sdk_provider.kwargs["base_url"] == "https://api.moonshot.ai/v1"
    assert sdk_provider.kwargs["use_responses"] is False
    assert run_config.model.model == "kimi-k2.5"


def test_openai_clients_are_not_opik_wrapped_without_explicit_backend(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    opik_module = ModuleType("opik")
    integrations_module = ModuleType("opik.integrations")
    openai_integration_module = ModuleType("opik.integrations.openai")

    def track_openai(client):
        calls.append(client)
        return client

    openai_integration_module.track_openai = track_openai
    monkeypatch.setitem(sys.modules, "opik", opik_module)
    monkeypatch.setitem(sys.modules, "opik.integrations", integrations_module)
    monkeypatch.setitem(sys.modules, "opik.integrations.openai", openai_integration_module)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")

    create_llm_backend(provider="openai")

    assert calls == []


def test_openai_clients_are_wrapped_only_when_opik_backend_is_enabled(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    opik_module = ModuleType("opik")
    integrations_module = ModuleType("opik.integrations")
    openai_integration_module = ModuleType("opik.integrations.openai")

    def track_openai(client):
        client.opik_wrapped = True
        calls.append(client)
        return client

    openai_integration_module.track_openai = track_openai
    monkeypatch.setitem(sys.modules, "opik", opik_module)
    monkeypatch.setitem(sys.modules, "opik.integrations", integrations_module)
    monkeypatch.setitem(sys.modules, "opik.integrations.openai", openai_integration_module)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")

    import seocho.tracing as tracing

    monkeypatch.setattr(tracing, "is_backend_enabled", lambda name: name == "opik")

    backend = create_llm_backend(provider="openai")

    assert len(calls) == 2
    assert getattr(backend._client, "opik_wrapped", False) is True
    assert getattr(backend._async_client, "opik_wrapped", False) is True


def test_openai_reasoning_model_uses_max_completion_tokens(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    backend = create_llm_backend(provider="openai", model="o4-mini")

    response = backend.complete(
        system="Reply exactly ok.",
        user="ok",
        temperature=0.0,
        max_tokens=12,
    )

    call = backend._client.chat.completions.calls[0]
    assert response.text == "ok"
    assert call["max_completion_tokens"] == 12
    assert "max_tokens" not in call
    assert "temperature" not in call


def test_non_openai_reasoning_provider_keeps_openai_compatible_token_parameter(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")
    backend = create_llm_backend(provider="deepseek", model="deepseek-reasoner")

    backend.complete(
        system="Reply exactly ok.",
        user="ok",
        temperature=0.25,
        max_tokens=12,
    )

    call = backend._client.chat.completions.calls[0]
    assert call["max_tokens"] == 12
    assert call["temperature"] == 0.25
    assert "max_completion_tokens" not in call


def test_deepseek_non_reasoning_request_disables_thinking_mode(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")
    backend = create_llm_backend(provider="deepseek", model="deepseek-v4-flash")

    backend.complete(
        system="Return valid json.",
        user="ok",
        temperature=0.0,
        response_format={"type": "json_object"},
        reasoning_mode=False,
        task_hint="intent_classification",
    )

    call = backend._client.chat.completions.calls[0]
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in call
    assert call["temperature"] == 0.0


def test_deepseek_reasoning_request_enables_thinking_and_effort(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")
    backend = create_llm_backend(provider="deepseek", model="deepseek-v4-pro")

    backend.complete(
        system="Return valid json.",
        user="ok",
        temperature=0.0,
        response_format={"type": "json_object"},
        reasoning_mode=True,
        task_hint="graph_cot",
    )

    call = backend._client.chat.completions.calls[0]
    assert call["extra_body"] == {"thinking": {"type": "enabled"}}
    assert call["reasoning_effort"] == "max"
    assert "temperature" not in call


def test_kimi_non_reasoning_request_uses_instant_mode_override(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONSHOT_API_KEY", "kimi-secret")
    backend = create_llm_backend(provider="kimi", model="kimi-k2.5")

    backend.complete(
        system="Return valid json.",
        user="ok",
        temperature=0.0,
        response_format={"type": "json_object"},
        reasoning_mode=False,
        task_hint="json_extraction",
    )

    call = backend._client.chat.completions.calls[0]
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    assert call["temperature"] == 0.6


def test_provider_retry_strips_reasoning_overrides_after_payload_rejection(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FallbackChatCompletions:
        def __init__(self) -> None:
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if "reasoning_effort" in kwargs or "extra_body" in kwargs:
                raise RuntimeError("unsupported provider override")
            if "response_format" in kwargs:
                raise RuntimeError("unsupported response format")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
                model=kwargs["model"],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            )

    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")
    backend = create_llm_backend(provider="deepseek", model="deepseek-v4-pro")
    backend._client.chat.completions = _FallbackChatCompletions()

    response = backend.complete(
        system="Return valid json.",
        user="ok",
        temperature=0.0,
        response_format={"type": "json_object"},
        reasoning_mode=True,
        task_hint="graph_cot",
    )

    calls = backend._client.chat.completions.calls
    assert response.json() == {"ok": True}
    assert len(calls) == 4
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert calls[0]["extra_body"] == {"thinking": {"type": "enabled"}}
    assert calls[0]["reasoning_effort"] == "max"
    assert calls[-1].get("response_format") is None
    assert "extra_body" not in calls[-1]
    assert "reasoning_effort" not in calls[-1]
    assert calls[-1]["messages"][0]["content"].endswith("Return ONLY valid JSON.")


# ---------------------------------------------------------------------------
# ADR-0098: vLLM on-prem profile (V1 preset + V2 factory + V5 smoke)
# ---------------------------------------------------------------------------


def test_vllm_provider_preset_resolves_localhost_default(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V1: vllm preset has localhost:8000/v1 base_url and the
    SEOCHO_VLLM_API_KEY env var."""
    monkeypatch.delenv("SEOCHO_VLLM_API_KEY", raising=False)
    monkeypatch.delenv("VLLM_API_KEY", raising=False)

    backend = create_llm_backend(provider="vllm", model="Qwen2.5-7B-Instruct")

    assert backend.provider == "vllm"
    assert backend.model == "Qwen2.5-7B-Instruct"
    assert backend._base_url == "http://localhost:8000/v1"
    assert backend._api_key_env == "SEOCHO_VLLM_API_KEY"


def test_vllm_falls_back_to_empty_api_key_when_unset(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V1: vLLM runs unauthenticated by default; backend passes the
    documented "EMPTY" sentinel so the OpenAI client doesn't refuse the
    request when no key is configured."""
    monkeypatch.delenv("SEOCHO_VLLM_API_KEY", raising=False)
    monkeypatch.delenv("VLLM_API_KEY", raising=False)

    backend = create_llm_backend(provider="vllm", model="Qwen2.5-7B-Instruct")

    assert backend._api_key == "EMPTY"
    assert backend._client.kwargs["api_key"] == "EMPTY"


def test_vllm_env_var_overrides_default(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V1: an explicit env-var key wins over the EMPTY default."""
    monkeypatch.setenv("SEOCHO_VLLM_API_KEY", "vllm-token")
    monkeypatch.delenv("VLLM_API_KEY", raising=False)

    backend = create_llm_backend(provider="vllm", model="Qwen2.5-7B-Instruct")

    assert backend._api_key == "vllm-token"


def test_vllm_legacy_env_alias(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V1: VLLM_API_KEY is the legacy alias for SEOCHO_VLLM_API_KEY."""
    monkeypatch.delenv("SEOCHO_VLLM_API_KEY", raising=False)
    monkeypatch.setenv("VLLM_API_KEY", "legacy-vllm-token")

    backend = create_llm_backend(provider="vllm", model="Qwen2.5-7B-Instruct")

    assert backend._api_key == "legacy-vllm-token"


def test_vllm_factory_requires_explicit_model(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V2: vllm provider has no sensible default_model — the factory
    raises if the caller doesn't pass one."""
    monkeypatch.delenv("SEOCHO_VLLM_API_KEY", raising=False)
    monkeypatch.delenv("VLLM_API_KEY", raising=False)

    with pytest.raises(ValueError, match="requires an explicit model"):
        create_llm_backend(provider="vllm")


def test_vllm_explicit_base_url_overrides_localhost_default(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V2: callers can point at any vLLM HTTP endpoint by passing
    base_url through the factory."""
    monkeypatch.delenv("SEOCHO_VLLM_API_KEY", raising=False)
    monkeypatch.delenv("VLLM_API_KEY", raising=False)

    backend = create_llm_backend(
        provider="vllm",
        model="Qwen2.5-7B-Instruct",
        base_url="https://vllm.internal.example:8443/v1",
    )

    assert backend._base_url == "https://vllm.internal.example:8443/v1"


def test_vllm_seocho_local_style_provider_slash_model_resolves(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V5 smoke: the ``Seocho.local(llm="vllm/<model>")`` codepath in
    seocho/client.py:370 splits on '/' and forwards provider+model to
    create_llm_backend. Verify the round-trip resolves to a working
    backend."""
    monkeypatch.delenv("SEOCHO_VLLM_API_KEY", raising=False)
    monkeypatch.delenv("VLLM_API_KEY", raising=False)

    llm_str = "vllm/Qwen2.5-7B-Instruct"
    provider, model = llm_str.split("/", 1)
    backend = create_llm_backend(provider=provider, model=model)

    assert backend.provider == "vllm"
    assert backend.model == "Qwen2.5-7B-Instruct"


def test_vllm_complete_round_trip_against_mocked_endpoint(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V5 smoke: complete() against the fake OpenAI client returns the
    canned 'ok' response and records the call with the right model."""
    monkeypatch.delenv("SEOCHO_VLLM_API_KEY", raising=False)
    monkeypatch.delenv("VLLM_API_KEY", raising=False)

    backend = create_llm_backend(provider="vllm", model="Qwen2.5-7B-Instruct")
    response = backend.complete(
        system="reply ok",
        user="ok",
        temperature=0.0,
        max_tokens=8,
    )

    assert response.text == "ok"
    call = backend._client.chat.completions.calls[0]
    assert call["model"] == "Qwen2.5-7B-Instruct"


def test_vllm_to_agents_sdk_model_binding(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V5 smoke: to_agents_sdk_model() binds the vLLM backend to the
    Agents SDK chat-completions adapter without complaint."""
    agents_module = ModuleType("agents")

    class FakeRunConfig:
        def __init__(self, *, model):
            self.model = model

    agents_module.RunConfig = FakeRunConfig
    monkeypatch.setitem(sys.modules, "agents", agents_module)

    chat_module = ModuleType("agents.models.openai_chatcompletions")

    class FakeAgentsModel:
        def __init__(self, *, model, openai_client):
            self.model = model
            self.openai_client = openai_client

    chat_module.OpenAIChatCompletionsModel = FakeAgentsModel
    monkeypatch.setitem(sys.modules, "agents.models.openai_chatcompletions", chat_module)

    provider_module = ModuleType("agents.models.openai_provider")

    class FakeProvider:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    provider_module.OpenAIProvider = FakeProvider
    monkeypatch.setitem(sys.modules, "agents.models.openai_provider", provider_module)

    monkeypatch.delenv("SEOCHO_VLLM_API_KEY", raising=False)
    monkeypatch.delenv("VLLM_API_KEY", raising=False)

    backend = VLLMBackend(model="Qwen2.5-7B-Instruct")
    sdk_model = backend.to_agents_sdk_model()
    sdk_provider = backend.to_agents_provider(use_responses=False)
    run_config = backend.to_agents_run_config()

    assert sdk_model.model == "Qwen2.5-7B-Instruct"
    assert sdk_provider.kwargs["base_url"] == "http://localhost:8000/v1"
    assert sdk_provider.kwargs["use_responses"] is False
    assert run_config.model.model == "Qwen2.5-7B-Instruct"
