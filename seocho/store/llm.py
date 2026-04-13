"""
LLM and embedding backend abstractions for the public SEOCHO SDK.

The default implementation uses OpenAI-compatible HTTP APIs so the same
interface can be reused across OpenAI, DeepSeek, Kimi, and Grok.
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Provider preset for OpenAI-compatible APIs."""

    name: str
    api_key_env: str
    base_url: str = ""
    default_model: str = "gpt-4o"
    default_embedding_model: Optional[str] = None
    supports_embeddings: bool = False


_PROVIDER_SPECS: Dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        name="openai",
        api_key_env="OPENAI_API_KEY",
        base_url="",
        default_model="gpt-4o",
        default_embedding_model="text-embedding-3-small",
        supports_embeddings=True,
    ),
    "deepseek": ProviderSpec(
        name="deepseek",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com",
        default_model="deepseek-chat",
        default_embedding_model=None,
        supports_embeddings=False,
    ),
    "kimi": ProviderSpec(
        name="kimi",
        api_key_env="MOONSHOT_API_KEY",
        base_url="https://api.moonshot.ai/v1",
        default_model="kimi-k2.5",
        default_embedding_model=None,
        supports_embeddings=False,
    ),
    "grok": ProviderSpec(
        name="grok",
        api_key_env="XAI_API_KEY",
        base_url="https://api.x.ai/v1",
        default_model="grok-4.20-reasoning",
        default_embedding_model=None,
        supports_embeddings=False,
    ),
}


def get_provider_spec(provider: str) -> ProviderSpec:
    """Return a provider preset by name."""

    key = str(provider).strip().lower() or "openai"
    try:
        return _PROVIDER_SPECS[key]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported LLM provider '{provider}'. "
            f"Known providers: {', '.join(sorted(_PROVIDER_SPECS))}"
        ) from exc


def list_provider_specs() -> Mapping[str, ProviderSpec]:
    """Return the known OpenAI-compatible provider presets."""

    return dict(_PROVIDER_SPECS)


def _strip_text(value: Optional[str]) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _resolve_client_kwargs(
    *,
    provider: str,
    api_key: Optional[str],
    base_url: Optional[str],
    timeout: float,
) -> tuple[ProviderSpec, Dict[str, Any], str, str]:
    spec = get_provider_spec(provider)
    resolved_base_url = _strip_text(base_url) or spec.base_url
    resolved_api_key = _strip_text(api_key) or _strip_text(os.getenv(spec.api_key_env))
    kwargs: Dict[str, Any] = {"timeout": timeout}
    if resolved_api_key:
        kwargs["api_key"] = resolved_api_key
    if resolved_base_url:
        kwargs["base_url"] = resolved_base_url
    return spec, kwargs, resolved_api_key, resolved_base_url


def _wrap_with_opik(client: Any) -> Any:
    try:
        from ..tracing import is_backend_enabled

        if not is_backend_enabled("opik"):
            return client
    except Exception:
        return client

    try:
        from opik.integrations.openai import track_openai

        return track_openai(client)
    except ImportError:
        return client
    except Exception:
        return client


@dataclass(slots=True)
class LLMResponse:
    """Structured response from an LLM call."""

    text: str
    model: str = ""
    usage: Dict[str, int] = field(default_factory=dict)

    def json(self) -> Any:
        """Parse the response text as JSON. Handles fenced code blocks."""
        text = self.text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            text = "\n".join(lines)
        return json.loads(text)


class LLMBackend(ABC):
    """Abstract interface for LLM completions."""

    @abstractmethod
    def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        """Synchronous completion."""

    @abstractmethod
    async def acomplete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        """Async completion."""


class EmbeddingBackend(ABC):
    """Abstract interface for embedding generation."""

    @abstractmethod
    def embed(
        self,
        texts: Sequence[str],
        *,
        model: Optional[str] = None,
    ) -> List[List[float]]:
        """Return embeddings for the provided texts."""


class OpenAICompatibleBackend(LLMBackend):
    """LLM backend for OpenAI-compatible chat-completions APIs."""

    def __init__(
        self,
        *,
        provider: str = "openai",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
    ) -> None:
        try:
            import openai
        except ImportError as exc:
            raise ImportError(
                "OpenAICompatibleBackend requires the 'openai' package. "
                "Install it with: pip install openai"
            ) from exc

        spec, kwargs, resolved_api_key, resolved_base_url = _resolve_client_kwargs(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
        client = _wrap_with_opik(openai.OpenAI(**kwargs))
        async_client = _wrap_with_opik(openai.AsyncOpenAI(**kwargs))

        self.provider = spec.name
        self.provider_spec = spec
        self.model = _strip_text(model) or spec.default_model
        self._api_key = resolved_api_key
        self._api_key_env = spec.api_key_env
        self._base_url = resolved_base_url
        self._timeout = timeout
        self._client = client
        self._async_client = async_client

    def _safe_temperature(self, temperature: float) -> float:
        """Clamp temperature for providers with restrictions.

        Kimi K2.5 only accepts temperature=1.  Rather than patching
        every call-site, we enforce the constraint here.
        """
        if self.provider == "kimi":
            return 1.0
        return temperature

    def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self._safe_temperature(temperature),
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format

        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception:
            # Fallback: some providers don't support response_format
            if response_format is not None:
                kwargs.pop("response_format", None)
                if "Return ONLY valid JSON" not in system:
                    kwargs["messages"][0]["content"] = system + "\n\nReturn ONLY valid JSON."
                resp = self._client.chat.completions.create(**kwargs)
            else:
                raise
        return self._build_response(resp)

    async def acomplete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self._safe_temperature(temperature),
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format

        try:
            resp = await self._async_client.chat.completions.create(**kwargs)
        except Exception:
            if response_format is not None:
                kwargs.pop("response_format", None)
                if "Return ONLY valid JSON" not in system:
                    kwargs["messages"][0]["content"] = system + "\n\nReturn ONLY valid JSON."
                resp = await self._async_client.chat.completions.create(**kwargs)
            else:
                raise
        return self._build_response(resp)

    def embed(
        self,
        texts: Sequence[str],
        *,
        model: Optional[str] = None,
    ) -> List[List[float]]:
        resolved_model = _strip_text(model) or _strip_text(self.provider_spec.default_embedding_model)
        if not resolved_model:
            raise ValueError(
                f"Provider '{self.provider}' does not define a default embedding model. "
                "Pass an explicit embedding model or use a dedicated embedding backend."
            )
        response = self._client.embeddings.create(
            model=resolved_model,
            input=list(texts),
        )
        return [list(item.embedding) for item in response.data]

    def to_embedding_backend(
        self,
        *,
        model: Optional[str] = None,
    ) -> "OpenAICompatibleEmbeddingBackend":
        return OpenAICompatibleEmbeddingBackend(
            provider=self.provider,
            model=model or self.provider_spec.default_embedding_model,
            api_key=self._api_key,
            base_url=self._base_url or None,
            timeout=self._timeout,
        )

    def to_agents_sdk_model(self, *, model: Optional[str] = None) -> Any:
        """Build an OpenAI Agents SDK model bound to this backend."""

        try:
            from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
        except ImportError as exc:
            raise ImportError(
                "OpenAI Agents SDK model support requires 'openai-agents'. "
                "Install it with: pip install openai-agents"
            ) from exc

        return OpenAIChatCompletionsModel(
            model=_strip_text(model) or self.model,
            openai_client=self._async_client,
        )

    def to_agents_provider(self, *, use_responses: Optional[bool] = None) -> Any:
        """Build an OpenAI Agents SDK provider bound to this backend."""

        try:
            from agents.models.openai_provider import OpenAIProvider
        except ImportError as exc:
            raise ImportError(
                "OpenAI Agents SDK provider support requires 'openai-agents'. "
                "Install it with: pip install openai-agents"
            ) from exc

        kwargs: Dict[str, Any] = {
            "api_key": self._api_key or None,
            "base_url": self._base_url or None,
        }
        if use_responses is not None:
            kwargs["use_responses"] = use_responses
        return OpenAIProvider(**kwargs)

    def to_agents_run_config(self, *, model: Optional[str] = None) -> Any:
        """Build a RunConfig that pins the Agents SDK to this backend."""

        try:
            from agents import RunConfig
        except ImportError as exc:
            raise ImportError(
                "OpenAI Agents SDK run config support requires 'openai-agents'. "
                "Install it with: pip install openai-agents"
            ) from exc

        return RunConfig(model=self.to_agents_sdk_model(model=model))

    @staticmethod
    def _build_response(resp: Any) -> LLMResponse:
        choice = resp.choices[0]
        usage = {}
        if getattr(resp, "usage", None):
            usage = {
                "prompt_tokens": int(getattr(resp.usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(resp.usage, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(resp.usage, "total_tokens", 0) or 0),
            }
        # Reasoning models (e.g. Kimi K2.5) may return the answer in
        # ``reasoning_content`` when ``content`` is empty — typically
        # when the generation was cut short by max_tokens.
        text = getattr(choice.message, "content", "") or ""
        if not text:
            text = getattr(choice.message, "reasoning_content", "") or ""
        return LLMResponse(
            text=text,
            model=getattr(resp, "model", "") or "",
            usage=usage,
        )

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(provider={self.provider!r}, "
            f"model={self.model!r})"
        )


class OpenAICompatibleEmbeddingBackend(EmbeddingBackend):
    """Embedding backend for OpenAI-compatible embedding APIs."""

    def __init__(
        self,
        *,
        provider: str = "openai",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
    ) -> None:
        try:
            import openai
        except ImportError as exc:
            raise ImportError(
                "OpenAICompatibleEmbeddingBackend requires the 'openai' package. "
                "Install it with: pip install openai"
            ) from exc

        spec, kwargs, resolved_api_key, resolved_base_url = _resolve_client_kwargs(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
        self.provider = spec.name
        self.provider_spec = spec
        self.model = _strip_text(model) or _strip_text(spec.default_embedding_model)
        if not self.model:
            raise ValueError(
                f"Provider '{self.provider}' does not define a default embedding model. "
                "Pass an explicit embedding model."
            )
        self._api_key = resolved_api_key
        self._api_key_env = spec.api_key_env
        self._base_url = resolved_base_url
        self._timeout = timeout
        self._client = _wrap_with_opik(openai.OpenAI(**kwargs))

    def embed(
        self,
        texts: Sequence[str],
        *,
        model: Optional[str] = None,
    ) -> List[List[float]]:
        resolved_model = _strip_text(model) or self.model
        response = self._client.embeddings.create(
            model=resolved_model,
            input=list(texts),
        )
        return [list(item.embedding) for item in response.data]

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(provider={self.provider!r}, "
            f"model={self.model!r})"
        )


class OpenAIBackend(OpenAICompatibleBackend):
    """Backwards-compatible OpenAI preset backend."""

    def __init__(
        self,
        *,
        model: str = "gpt-4o",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(
            provider="openai",
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )


class DeepSeekBackend(OpenAICompatibleBackend):
    def __init__(
        self,
        *,
        model: str = "deepseek-chat",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(
            provider="deepseek",
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )


class KimiBackend(OpenAICompatibleBackend):
    def __init__(
        self,
        *,
        model: str = "kimi-k2.5",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(
            provider="kimi",
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )


class GrokBackend(OpenAICompatibleBackend):
    def __init__(
        self,
        *,
        model: str = "grok-4.20-reasoning",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(
            provider="grok",
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )


def create_llm_backend(
    *,
    provider: str = "openai",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 120.0,
) -> OpenAICompatibleBackend:
    """Create an OpenAI-compatible LLM backend by provider preset."""

    provider_key = str(provider).strip().lower() or "openai"
    if provider_key == "openai":
        return OpenAIBackend(
            model=model or get_provider_spec("openai").default_model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
    if provider_key == "deepseek":
        return DeepSeekBackend(
            model=model or get_provider_spec("deepseek").default_model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
    if provider_key == "kimi":
        return KimiBackend(
            model=model or get_provider_spec("kimi").default_model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
    if provider_key == "grok":
        return GrokBackend(
            model=model or get_provider_spec("grok").default_model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
    raise ValueError(
        f"Unsupported LLM provider '{provider}'. "
        f"Known providers: {', '.join(sorted(_PROVIDER_SPECS))}"
    )


def create_embedding_backend(
    *,
    provider: str = "openai",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 120.0,
) -> OpenAICompatibleEmbeddingBackend:
    """Create an embedding backend by provider preset."""

    return OpenAICompatibleEmbeddingBackend(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
    )
