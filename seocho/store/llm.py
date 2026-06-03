"""
LLM and embedding backend abstractions for the public SEOCHO SDK.

The default implementation uses OpenAI-compatible HTTP APIs so the same
interface can be reused across OpenAI, DeepSeek, Kimi, Grok, and Qwen.
"""

from __future__ import annotations

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Provider preset for OpenAI-compatible APIs.

    Capability fields are declarative so per-provider request quirks are *data*
    read once at backend construction, not imperative ``if provider == ...``
    branches scattered at call sites (CLAUDE.md §7 centralized config). For the
    one real intra-provider split today (OpenAI reasoning models use a different
    token-param and ignore temperature) we keep a model-prefix override rather
    than a per-model table — see ``resolve_model_caps``.
    """

    name: str
    api_key_env: str
    api_key_env_aliases: tuple[str, ...] = ()
    base_url: str = ""
    default_model: str = "gpt-4o"
    default_embedding_model: Optional[str] = None
    supports_embeddings: bool = False
    # --- capability fields (declarative; read once in OpenAICompatibleBackend) ---
    tier: str = "chat"  # "chat" | "reasoning"
    temperature_policy: str = "free"  # "free" | "fixed_1" | "ignored"
    token_param: str = "max_tokens"  # "max_tokens" | "max_completion_tokens"
    supports_response_format: bool = True
    latency_tier: str = "mid"  # "fast" | "mid" | "slow"
    supports_prompt_cache: bool = False


_PROVIDER_SPECS: Dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        name="openai",
        api_key_env="OPENAI_API_KEY",
        base_url="",
        default_model="gpt-4o",
        default_embedding_model="text-embedding-3-small",
        supports_embeddings=True,
        tier="chat",
        temperature_policy="free",
        token_param="max_tokens",
        supports_response_format=True,
        latency_tier="mid",
        supports_prompt_cache=True,  # OpenAI auto prefix caching ≥1024 tokens
    ),
    "deepseek": ProviderSpec(
        name="deepseek",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com",
        default_model="deepseek-chat",
        default_embedding_model=None,
        supports_embeddings=False,
        tier="chat",
        temperature_policy="free",
        token_param="max_tokens",
        supports_response_format=True,
        latency_tier="fast",
        supports_prompt_cache=True,  # DeepSeek context caching
    ),
    "kimi": ProviderSpec(
        name="kimi",
        api_key_env="MOONSHOT_API_KEY",
        base_url="https://api.moonshot.ai/v1",
        default_model="kimi-k2.5",
        default_embedding_model=None,
        supports_embeddings=False,
        tier="chat",
        temperature_policy="fixed_1",  # Kimi K2.5 only accepts temperature=1.0
        token_param="max_tokens",
        supports_response_format=True,
        latency_tier="mid",
        supports_prompt_cache=False,
    ),
    "grok": ProviderSpec(
        name="grok",
        api_key_env="XAI_API_KEY",
        api_key_env_aliases=("GROK_API_KEY",),
        base_url="https://api.x.ai/v1",
        default_model="grok-4.20-reasoning",
        default_embedding_model=None,
        supports_embeddings=False,
        tier="chat",
        temperature_policy="free",
        token_param="max_tokens",
        supports_response_format=True,
        latency_tier="slow",
        supports_prompt_cache=False,
    ),
    "qwen": ProviderSpec(
        name="qwen",
        api_key_env="DASHSCOPE_API_KEY",
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-plus",
        default_embedding_model=None,
        supports_embeddings=False,
        tier="chat",
        temperature_policy="free",
        token_param="max_tokens",
        supports_response_format=True,
        latency_tier="mid",
        supports_prompt_cache=False,
    ),
    "mara": ProviderSpec(
        name="mara",
        api_key_env="MARA_API_KEY",
        # MARA cloud OpenAI-compatible gateway; serves DeepSeek-V3.1,
        # MiniMax-M2.5, gpt-oss-120b under one endpoint/key.
        base_url="https://api.cloud.mara.com/v1",
        default_model="DeepSeek-V3.1",
        default_embedding_model=None,
        supports_embeddings=False,
        tier="chat",
        temperature_policy="free",
        token_param="max_tokens",
        # Gateway-served OSS models vary in JSON discipline; keep True but lean
        # on the response_format try/except fallback in complete().
        supports_response_format=True,
        latency_tier="mid",
        supports_prompt_cache=False,
    ),
}


def resolve_model_caps(spec: ProviderSpec, model: str) -> Dict[str, Any]:
    """Resolve effective per-(provider, model) capabilities.

    Starts from the declarative provider spec and applies the one model-prefix
    override we actually need: OpenAI reasoning models (o1/o3/o4/gpt-5) use
    ``max_completion_tokens`` and ignore ``temperature``. Kept as a spec-driven
    override rather than a per-model table (only one intra-provider split exists
    today).
    """
    caps: Dict[str, Any] = {
        "tier": spec.tier,
        "temperature_policy": spec.temperature_policy,
        "token_param": spec.token_param,
        "supports_response_format": spec.supports_response_format,
    }
    model_lower = str(model or "").strip().lower()
    if spec.name == "openai" and model_lower.startswith(("o1", "o3", "o4", "gpt-5")):
        caps["tier"] = "reasoning"
        caps["temperature_policy"] = "ignored"
        caps["token_param"] = "max_completion_tokens"
    return caps


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
    resolved_api_key = _strip_text(api_key)
    if not resolved_api_key:
        for env_name in (spec.api_key_env, *spec.api_key_env_aliases):
            resolved_api_key = _strip_text(os.getenv(env_name))
            if resolved_api_key:
                break
    # max_retries=0: the OpenAI SDK's built-in retry (default 2) multiplies with
    # our own _create_with_retry, turning one unresponsive gateway call into a
    # timeout×3×N storm (observed: a hung MARA DeepSeek call stalled a run ~23min).
    # We own retry/backoff explicitly, so disable the SDK's.
    kwargs: Dict[str, Any] = {"timeout": timeout, "max_retries": 0}
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

    def chat(
        self,
        text: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """Single-shot convenience for notebooks / REPL.

        Production code should call :meth:`complete` directly with explicit
        ``system`` and ``user`` roles. This shortcut supplies a benign default
        ``system`` so quick demos and provider comparisons don't have to.
        """
        return self.complete(
            system=system or "You are a careful, concise assistant.",
            user=text,
            temperature=temperature,
            max_tokens=max_tokens,
        )


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
        # Resolve per-(provider, model) capabilities once; request kwargs read
        # this instead of branching on provider/model strings per call.
        self._caps = resolve_model_caps(spec, self.model)
        self._api_key = resolved_api_key
        self._api_key_env = spec.api_key_env
        self._base_url = resolved_base_url
        self._timeout = timeout
        self._client = client
        self._async_client = async_client

    def _safe_temperature(self, temperature: float) -> float:
        """Clamp temperature per the resolved capability policy.

        ``fixed_1`` providers (e.g. Kimi K2.5) only accept temperature=1.0.
        """
        if self._caps["temperature_policy"] == "fixed_1":
            return 1.0
        return temperature

    def _uses_openai_reasoning_parameters(self) -> bool:
        """Return true when the resolved model uses reasoning request quirks.

        (``max_completion_tokens`` + temperature ignored — e.g. OpenAI o-series.)
        """
        return self._caps["token_param"] == "max_completion_tokens"

    def _completion_request_kwargs(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: Optional[int],
        response_format: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        caps = self._caps
        if caps["temperature_policy"] != "ignored":
            kwargs["temperature"] = self._safe_temperature(temperature)
        if max_tokens is not None:
            kwargs[caps["token_param"]] = max_tokens
        if response_format is not None and caps["supports_response_format"]:
            kwargs["response_format"] = response_format
        return kwargs

    # Retryable transient API errors (rate limits, timeouts, 5xx). Matched by
    # class name so we don't hard-depend on a specific openai SDK version.
    _RETRYABLE_ERRORS = (
        "RateLimitError",
        "APITimeoutError",
        "APIConnectionError",
        "InternalServerError",
        "APIError",
    )
    _MAX_RETRIES = 5       # rate-limit (429) retries — clears with backoff
    _TIMEOUT_RETRIES = 2   # timeout/connection retries — fail fast, don't stall

    def _create_with_retry(self, kwargs: Dict[str, Any]):
        """Call chat.completions.create with exponential backoff on transient
        errors (e.g. gateway 429 rate limits). Non-retryable errors (bad request,
        insufficient_quota) raise immediately so callers can react. We never
        silently degrade — backoff exhaustion re-raises (§20.2)."""
        delay = 2.0
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                return self._client.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001
                name = type(exc).__name__
                # insufficient_quota is a billing state, not transient — don't retry.
                is_quota = "insufficient_quota" in str(exc).lower()
                # Rate limits clear with backoff → retry up to the full budget.
                # Timeout/connection errors usually mean the gateway is wedged on
                # THIS input → a few quick retries then fail fast so the run moves
                # on and records the failure (§20.2), rather than a minutes-long
                # stall retrying an unresponsive call.
                is_rate_limit = name == "RateLimitError"
                cap = self._MAX_RETRIES if is_rate_limit else self._TIMEOUT_RETRIES
                if name in self._RETRYABLE_ERRORS and not is_quota and attempt < cap:
                    logger.warning(
                        "transient LLM error (%s) on %s, retry %d/%d after %.1fs",
                        name, self.model, attempt + 1, cap, delay,
                    )
                    time.sleep(delay)
                    delay = min(delay * 2, 30.0)
                    continue
                raise

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
        kwargs = self._completion_request_kwargs(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )

        try:
            resp = self._create_with_retry(kwargs)
        except Exception:
            # Fallback: some providers don't support response_format
            if response_format is not None:
                kwargs.pop("response_format", None)
                if "Return ONLY valid JSON" not in system:
                    kwargs["messages"][0]["content"] = system + "\n\nReturn ONLY valid JSON."
                resp = self._create_with_retry(kwargs)
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
        kwargs = self._completion_request_kwargs(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )

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
            u = resp.usage
            usage = {
                "prompt_tokens": int(getattr(u, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(u, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(u, "total_tokens", 0) or 0),
            }
            # Provider-reported prompt-cache hits (KV-cache telemetry for the
            # cost-efficiency analysis): OpenAI nests it under
            # prompt_tokens_details.cached_tokens; DeepSeek reports
            # prompt_cache_hit_tokens at the top level.
            cached = 0
            details = getattr(u, "prompt_tokens_details", None)
            if details is not None:
                cached = int(getattr(details, "cached_tokens", 0) or 0)
            if not cached:
                cached = int(getattr(u, "prompt_cache_hit_tokens", 0) or 0)
            usage["cached_tokens"] = cached
            # I/O-vs-compute split (some gateways, e.g. MARA, report serving
            # timing on usage): time_to_first_token ≈ PREFILL (compute, prompt-
            # bound, cacheable); completion_tokens_per_sec ≈ DECODE (memory-
            # bandwidth, sequential); total_latency = provider-side wall. Lets us
            # attribute LLM latency to prefill vs decode vs network (USE method).
            for src, dst in (("time_to_first_token", "ttft_s"),
                             ("completion_tokens_per_sec", "decode_tok_per_s"),
                             ("total_latency", "provider_latency_s")):
                val = getattr(u, src, None)
                if val is not None:
                    try:
                        usage[dst] = float(val)
                    except (TypeError, ValueError):
                        pass
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


class QwenBackend(OpenAICompatibleBackend):
    def __init__(
        self,
        *,
        model: str = "qwen-plus",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(
            provider="qwen",
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
    if provider_key == "qwen":
        return QwenBackend(
            model=model or get_provider_spec("qwen").default_model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
    # Generic spec-driven fallback: any provider registered in _PROVIDER_SPECS
    # (e.g. mara) without a dedicated subclass is served by the base
    # OpenAI-compatible backend using its preset base_url/key/default_model.
    if provider_key in _PROVIDER_SPECS:
        return OpenAICompatibleBackend(
            provider=provider_key,
            model=model or get_provider_spec(provider_key).default_model,
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
