"""A missing LLM credential must fail with a SEOCHO-native message.

Before this guard, ``Seocho.local(onto)`` with no key crashed inside the
``openai`` client with an ``OPENAI_API_KEY``-branded error even when the
selected provider was MARA — the first-run experience pointed users at the
wrong environment variable. Construction now raises
:class:`seocho.exceptions.SeochoCredentialError` naming the actual provider
and the env var(s) SEOCHO resolves for it.
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from seocho.exceptions import SeochoCredentialError, SeochoError
from seocho.store.llm import create_llm_backend


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("openai")
    module.OpenAI = lambda **kwargs: SimpleNamespace(kwargs=kwargs)
    module.AsyncOpenAI = lambda **kwargs: SimpleNamespace(kwargs=kwargs)
    monkeypatch.setitem(sys.modules, "openai", module)

_ALL_KEY_ENVS = [
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "MOONSHOT_API_KEY",
    "XAI_API_KEY",
    "GROK_API_KEY",
    "DASHSCOPE_API_KEY",
    "MARA_API_KEY",
    "SEOCHO_VLLM_API_KEY",
    "VLLM_API_KEY",
]


@pytest.fixture(autouse=True)
def _no_provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in _ALL_KEY_ENVS:
        monkeypatch.delenv(env, raising=False)


def test_mara_without_key_raises_seocho_error_naming_mara_env() -> None:
    with pytest.raises(SeochoCredentialError) as exc:
        create_llm_backend(provider="mara")

    message = str(exc.value)
    assert "mara" in message
    assert "MARA_API_KEY" in message
    # The whole point: never point a MARA user at OpenAI's variable.
    assert "OPENAI_API_KEY" not in message


def test_message_lists_env_aliases() -> None:
    with pytest.raises(SeochoCredentialError) as exc:
        create_llm_backend(provider="grok")

    message = str(exc.value)
    assert "XAI_API_KEY" in message
    assert "GROK_API_KEY" in message


def test_credential_error_is_a_seocho_error() -> None:
    # Users catching the documented base class keep working.
    with pytest.raises(SeochoError):
        create_llm_backend(provider="openai")


def test_vllm_stays_keyless_via_empty_sentinel(fake_openai: None) -> None:
    # vLLM runs unauthenticated by default; its backend passes the documented
    # "EMPTY" sentinel, so the credential guard must not fire.
    backend = create_llm_backend(provider="vllm", model="Qwen2.5-7B-Instruct")
    assert backend._api_key == "EMPTY"


def test_explicit_empty_opts_out_for_local_gateways(fake_openai: None) -> None:
    backend = create_llm_backend(provider="openai", api_key="EMPTY")
    assert backend._api_key == "EMPTY"
