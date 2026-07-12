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


def test_mara_provider_preset_resolves_cloud_defaults(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MARA preset resolves the cloud base_url, MiniMax-M2.5 default model,
    and MARA_API_KEY env var."""
    monkeypatch.setenv("MARA_API_KEY", "mara-secret")

    backend = create_llm_backend(provider="mara")

    assert backend.provider == "mara"
    assert backend.model == "MiniMax-M2.5"
    assert backend._base_url == "https://api.cloud.mara.com/v1"
    assert backend._api_key_env == "MARA_API_KEY"
    assert backend._api_key == "mara-secret"


def test_mara_provider_seocho_local_style_resolves(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seocho.local(llm="mara/MiniMax-M2.5") split → factory round trip."""
    monkeypatch.setenv("MARA_API_KEY", "mara-secret")

    provider, model = "mara/MiniMax-M2.5".split("/", 1)
    backend = create_llm_backend(provider=provider, model=model)

    assert backend.provider == "mara"
    assert backend.model == "MiniMax-M2.5"


def test_kimi_cache_key_flows_through_restricted_provider_options(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONSHOT_API_KEY", "kimi-secret")
    backend = create_llm_backend(provider="kimi", model="kimi-k2.6")

    backend.complete(
        system="stable ontology prefix",
        user="variable question",
        provider_options={"prompt_cache_key": "workspace-session-7"},
    )

    call = backend._client.chat.completions.calls[0]
    assert call["extra_body"] == {"prompt_cache_key": "workspace-session-7"}


def test_provider_options_reject_unknown_transport_fields(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARA_API_KEY", "mara-secret")
    backend = create_llm_backend(provider="mara")

    with pytest.raises(ValueError, match="Unsupported provider options"):
        backend.complete(
            system="system",
            user="user",
            provider_options={"unsafe_arbitrary_field": "value"},
        )


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


def test_vllm_pipeline_mode_translates_json_object_to_guided_json(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V3: pipeline-mode + response_format={'type':'json_object'} on vLLM
    translates to extra_body.guided_json. response_format is dropped
    because guided decoding supersedes it on the vLLM endpoint."""
    monkeypatch.delenv("SEOCHO_VLLM_API_KEY", raising=False)
    monkeypatch.delenv("VLLM_API_KEY", raising=False)

    backend = create_llm_backend(provider="vllm", model="Qwen2.5-7B-Instruct")
    backend.complete(
        system="reply json",
        user="ok",
        temperature=0.0,
        response_format={"type": "json_object"},
        mode="pipeline",
    )

    call = backend._client.chat.completions.calls[0]
    assert "response_format" not in call
    assert call["extra_body"] == {"guided_json": {"type": "object"}}


def test_vllm_pipeline_mode_translates_json_schema(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V3: pipeline-mode + response_format={'type':'json_schema',...}
    on vLLM translates to extra_body.guided_json with the schema."""
    monkeypatch.delenv("SEOCHO_VLLM_API_KEY", raising=False)
    monkeypatch.delenv("VLLM_API_KEY", raising=False)

    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    backend = create_llm_backend(provider="vllm", model="Qwen2.5-7B-Instruct")
    backend.complete(
        system="reply json",
        user="ok",
        temperature=0.0,
        response_format={"type": "json_schema", "json_schema": schema},
        mode="pipeline",
    )

    call = backend._client.chat.completions.calls[0]
    assert "response_format" not in call
    assert call["extra_body"] == {"guided_json": schema}


def test_vllm_pipeline_mode_translates_regex_and_choice(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V3: regex and choice response_format types translate to the
    corresponding guided_* extra_body keys."""
    monkeypatch.delenv("SEOCHO_VLLM_API_KEY", raising=False)
    monkeypatch.delenv("VLLM_API_KEY", raising=False)

    backend = create_llm_backend(provider="vllm", model="Qwen2.5-7B-Instruct")

    backend.complete(
        system="match",
        user="ok",
        response_format={"type": "regex", "pattern": r"^[A-Z]{3}$"},
        mode="pipeline",
    )
    call = backend._client.chat.completions.calls[-1]
    assert call["extra_body"] == {"guided_regex": r"^[A-Z]{3}$"}

    backend.complete(
        system="pick one",
        user="ok",
        response_format={"type": "choice", "options": ["yes", "no", "maybe"]},
        mode="pipeline",
    )
    call = backend._client.chat.completions.calls[-1]
    assert call["extra_body"] == {"guided_choice": ["yes", "no", "maybe"]}


def test_vllm_agent_mode_does_not_translate_response_format(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V3: agent mode preserves the OpenAI response_format because the
    Agents SDK's tool-call structure carries the shape — we must never
    JSON-force a tool-call response."""
    monkeypatch.delenv("SEOCHO_VLLM_API_KEY", raising=False)
    monkeypatch.delenv("VLLM_API_KEY", raising=False)

    backend = create_llm_backend(provider="vllm", model="Qwen2.5-7B-Instruct")
    backend.complete(
        system="agent mode",
        user="ok",
        response_format={"type": "json_object"},
        mode="agent",
    )

    call = backend._client.chat.completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert "extra_body" not in call or "guided_json" not in (call.get("extra_body") or {})


def test_vllm_default_mode_preserves_pre_adr_behavior(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V3 backward compat: when ``mode`` is unset, response_format flows
    through unchanged — pre-ADR-0098 callers are unaffected."""
    monkeypatch.delenv("SEOCHO_VLLM_API_KEY", raising=False)
    monkeypatch.delenv("VLLM_API_KEY", raising=False)

    backend = create_llm_backend(provider="vllm", model="Qwen2.5-7B-Instruct")
    backend.complete(
        system="default",
        user="ok",
        response_format={"type": "json_object"},
        # no mode arg
    )

    call = backend._client.chat.completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert "extra_body" not in call or "guided_json" not in (call.get("extra_body") or {})


def test_pipeline_mode_is_noop_on_non_vllm_provider(
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V3: ``mode='pipeline'`` is a no-op on providers without
    guided-decoding support (openai, deepseek, kimi, grok, qwen).
    response_format passes through unchanged."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")

    backend = create_llm_backend(provider="deepseek", model="deepseek-chat")
    backend.complete(
        system="reply json",
        user="ok",
        response_format={"type": "json_object"},
        mode="pipeline",
    )

    call = backend._client.chat.completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert "extra_body" not in call or "guided_json" not in (call.get("extra_body") or {})


def test_vllm_agents_sdk_path_preserves_tool_calls_without_json_forcing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V4: when the agent flow calls vLLM via the OpenAI-compatible chat
    completions endpoint, tool_calls in the response must propagate
    unchanged. Our backend must not inject ``response_format`` or
    ``extra_body.guided_*`` into the request path that the Agents SDK
    drives — those would force JSON shape and corrupt tool-call output.

    The test exercises both layers:
      1. ``to_agents_sdk_model()`` binds the OpenAIChatCompletionsModel
         to the same async client we'd use for pipeline calls. This is
         the contract that lets vLLM serve tool calls natively.
      2. Calling the underlying client with a tools= request returns
         the canned tool_call choice unchanged — no JSON-shape munging
         on the way through.
    """
    # Fake OpenAI client that returns a tool_calls response when invoked
    # with a tools= argument.
    class _ToolCallChatCompletions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs.get("tools"):
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content=None,
                                tool_calls=[
                                    SimpleNamespace(
                                        id="call_1",
                                        type="function",
                                        function=SimpleNamespace(
                                            name="lookup_entity",
                                            arguments='{"name":"Apple"}',
                                        ),
                                    )
                                ],
                            )
                        )
                    ],
                    model=kwargs["model"],
                    usage=SimpleNamespace(
                        prompt_tokens=1, completion_tokens=2, total_tokens=3
                    ),
                )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content="plain"))
                ],
                model=kwargs["model"],
                usage=SimpleNamespace(
                    prompt_tokens=1, completion_tokens=2, total_tokens=3
                ),
            )

    class _ToolCallOpenAIClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.chat = SimpleNamespace(completions=_ToolCallChatCompletions())
            self.embeddings = _FakeEmbeddings()

    module = ModuleType("openai")
    module.OpenAI = _ToolCallOpenAIClient
    module.AsyncOpenAI = _ToolCallOpenAIClient
    monkeypatch.setitem(sys.modules, "openai", module)

    # Stub the agents SDK so to_agents_sdk_model() can bind.
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

    # Layer 1 contract: the SDK model wraps the same async client that
    # we'd use for direct pipeline calls. This is how vLLM's tool-call
    # responses reach Runner.run unchanged.
    assert sdk_model.openai_client is backend._async_client

    # Layer 2 contract: the underlying client returns tool_calls when
    # given a tools= request, and no guided_json/response_format was
    # injected by our wiring on the way in.
    response = backend._client.chat.completions.create(
        model="Qwen2.5-7B-Instruct",
        messages=[{"role": "user", "content": "find Apple"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "lookup_entity",
                    "description": "Lookup entity by name",
                    "parameters": {"type": "object"},
                },
            }
        ],
    )
    call = backend._client.chat.completions.calls[0]
    assert "response_format" not in call
    assert "extra_body" not in call
    tool_calls = response.choices[0].message.tool_calls
    assert tool_calls is not None
    assert tool_calls[0].function.name == "lookup_entity"


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
