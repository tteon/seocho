from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from seocho.store.llm import (
    KimiBackend,
    create_embedding_backend,
    create_llm_backend,
)
from seocho.tracing import disable_tracing


class _FakeChatCompletions:
    def create(self, **kwargs):
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
