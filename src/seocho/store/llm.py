"""
LLM and embedding backend abstractions for the public SEOCHO SDK.

The default implementation uses OpenAI-compatible HTTP APIs so the same
interface can be reused across OpenAI, DeepSeek, Kimi, Grok, and Qwen.
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
    api_key_env_aliases: tuple[str, ...] = ()
    base_url: str = ""
    default_model: str = "gpt-4o"
    default_embedding_model: Optional[str] = None
    supports_embeddings: bool = False
    # Per-provider default request timeout (seconds). Reasoning-model presets
    # override the 120s baseline because single-document extraction routinely
    # runs much longer and was silently tripping the heuristic fallback (#118).
    default_timeout: float = 120.0


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
        # kimi-k2.5 single-document extraction was measured at 160-1450s; 120s
        # cut it off and silently degraded to heuristic extraction (#118).
        default_timeout=900.0,
    ),
    "grok": ProviderSpec(
        name="grok",
        api_key_env="XAI_API_KEY",
        api_key_env_aliases=("GROK_API_KEY",),
        base_url="https://api.x.ai/v1",
        default_model="grok-4.20-reasoning",
        default_embedding_model=None,
        supports_embeddings=False,
        # Default model is a reasoning preset — give it the same headroom.
        default_timeout=900.0,
    ),
    "qwen": ProviderSpec(
        name="qwen",
        api_key_env="DASHSCOPE_API_KEY",
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-plus",
        default_embedding_model=None,
        supports_embeddings=False,
    ),
    # ADR-0098: vLLM on-prem profile. base_url defaults to vLLM's
    # local server convention; api_key is optional (vLLM runs
    # unauthenticated by default — VLLMBackend passes "EMPTY" to the
    # OpenAI client when no key is found). default_model intentionally
    # blank: the model is operator-chosen (e.g. "Qwen2.5-7B-Instruct")
    # and Seocho.local(llm="vllm/<model>") requires the explicit name.
    "vllm": ProviderSpec(
        name="vllm",
        api_key_env="SEOCHO_VLLM_API_KEY",
        api_key_env_aliases=("VLLM_API_KEY",),
        base_url="http://localhost:8000/v1",
        default_model="",
        default_embedding_model=None,
        supports_embeddings=False,
    ),
    # MARA cloud — OpenAI-compatible endpoint serving MiniMax-class models.
    "mara": ProviderSpec(
        name="mara",
        api_key_env="MARA_API_KEY",
        base_url="https://api.cloud.mara.com/v1",
        default_model="MiniMax-M2.5",
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
    resolved_api_key = _strip_text(api_key)
    if not resolved_api_key:
        for env_name in (spec.api_key_env, *spec.api_key_env_aliases):
            resolved_api_key = _strip_text(os.getenv(env_name))
            if resolved_api_key:
                break
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
        reasoning_mode: Optional[bool] = None,
        task_hint: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> LLMResponse:
        """Synchronous completion.

        ADR-0098: ``mode`` is "pipeline" or "agent" (case-insensitive).
        In pipeline mode against a vLLM provider, ``response_format`` is
        translated into ``extra_body.guided_*`` so structured output
        becomes deterministic rather than relying on the prompt-injection
        fallback. In agent mode the Agents SDK's tool-call structure
        supersedes guided decoding and no translation happens. None
        preserves pre-ADR-0098 behavior for callers that don't opt in.
        """

    @abstractmethod
    async def acomplete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, Any]] = None,
        reasoning_mode: Optional[bool] = None,
        task_hint: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> LLMResponse:
        """Async completion. See :meth:`complete` for the ``mode`` contract."""

    def chat(
        self,
        text: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        reasoning_mode: Optional[bool] = None,
        task_hint: Optional[str] = None,
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
            reasoning_mode=reasoning_mode,
            task_hint=task_hint,
        )


def complete_with_task_hints(
    llm: Any,
    *,
    system: str,
    user: str,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    response_format: Optional[Dict[str, Any]] = None,
    reasoning_mode: Optional[bool] = None,
    task_hint: Optional[str] = None,
    mode: Optional[str] = None,
) -> Any:
    """Call ``llm.complete`` while remaining compatible with older test doubles."""

    kwargs: Dict[str, Any] = {
        "system": system,
        "user": user,
        "temperature": temperature,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if response_format is not None:
        kwargs["response_format"] = response_format
    if reasoning_mode is not None:
        kwargs["reasoning_mode"] = reasoning_mode
    if task_hint is not None:
        kwargs["task_hint"] = task_hint
    if mode is not None:
        kwargs["mode"] = mode
    try:
        return llm.complete(**kwargs)
    except TypeError as exc:
        if "unexpected keyword argument" not in str(exc):
            raise
        # Older backends predate mode/reasoning_mode/task_hint — strip
        # them and retry so this helper stays drop-in for legacy doubles.
        kwargs.pop("reasoning_mode", None)
        kwargs.pop("task_hint", None)
        kwargs.pop("mode", None)
        return llm.complete(**kwargs)


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

    def _safe_temperature(
        self,
        temperature: float,
        *,
        reasoning_mode: Optional[bool] = None,
    ) -> float:
        """Clamp temperature for providers with restrictions.

        Kimi requires provider-specific temperatures for both instant and
        thinking modes. Keep the coercion centralized so callers can keep using
        the repo-wide deterministic defaults.
        """
        if self.provider == "kimi" and float(temperature) == 0.0:
            if reasoning_mode is False:
                return 0.6
            if reasoning_mode:
                return 1.0
        return temperature

    @staticmethod
    def _merge_extra_body(
        current: Optional[Dict[str, Any]],
        updates: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not current and not updates:
            return None
        merged: Dict[str, Any] = {}
        if isinstance(current, dict):
            merged.update(current)
        if isinstance(updates, dict):
            for key, value in updates.items():
                if isinstance(value, dict) and isinstance(merged.get(key), dict):
                    nested = dict(merged[key])
                    nested.update(value)
                    merged[key] = nested
                else:
                    merged[key] = value
        return merged

    def _reasoning_request_overrides(
        self,
        *,
        reasoning_mode: Optional[bool],
        task_hint: Optional[str],
    ) -> Dict[str, Any]:
        task = _strip_text(task_hint).lower()
        kwargs: Dict[str, Any] = {}
        if self.provider == "deepseek":
            if reasoning_mode is not None:
                kwargs["extra_body"] = {
                    "thinking": {"type": "enabled" if reasoning_mode else "disabled"}
                }
            if reasoning_mode:
                kwargs["reasoning_effort"] = (
                    "max"
                    if task in {"graph_cot", "tool_agent", "tool_loop"}
                    else "high"
                )
        elif self.provider == "kimi" and reasoning_mode is False:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        return kwargs

    def _uses_openai_reasoning_parameters(self) -> bool:
        """Return true for OpenAI reasoning models with chat-completions quirks."""
        if self.provider != "openai":
            return False
        model = self.model.strip().lower()
        return model.startswith(("o1", "o3", "o4", "gpt-5"))

    @staticmethod
    def _translate_response_format_to_guided(
        response_format: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Translate an OpenAI ``response_format`` into a vLLM guided-decoding
        ``extra_body`` payload (ADR-0098). Returns None if the format is
        not translatable so the caller leaves ``response_format`` in place.

        Mapping per ADR-0098 §3:
            {"type":"json_object"}             → {"guided_json": {"type":"object"}}
            {"type":"json_schema","json_schema":S} → {"guided_json": S}
            {"type":"regex","pattern":P}       → {"guided_regex": P}
            {"type":"choice","options":[...]}  → {"guided_choice": [...]}
        """
        if not isinstance(response_format, dict):
            return None
        rf_type = str(response_format.get("type") or "").lower()
        if rf_type == "json_object":
            return {"guided_json": {"type": "object"}}
        if rf_type == "json_schema":
            schema = response_format.get("json_schema") or response_format.get("schema")
            if schema is None:
                return None
            return {"guided_json": schema}
        if rf_type == "regex":
            pattern = response_format.get("pattern")
            if pattern is None:
                return None
            return {"guided_regex": pattern}
        if rf_type == "choice":
            options = response_format.get("options")
            if options is None:
                return None
            return {"guided_choice": list(options)}
        return None

    def _completion_request_kwargs(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: Optional[int],
        response_format: Optional[Dict[str, Any]],
        reasoning_mode: Optional[bool],
        task_hint: Optional[str],
        mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        reasoning_overrides = self._reasoning_request_overrides(
            reasoning_mode=reasoning_mode,
            task_hint=task_hint,
        )
        if self._uses_openai_reasoning_parameters():
            if max_tokens is not None:
                kwargs["max_completion_tokens"] = max_tokens
        else:
            if not (self.provider == "deepseek" and reasoning_mode):
                kwargs["temperature"] = self._safe_temperature(
                    temperature,
                    reasoning_mode=reasoning_mode,
                )
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens

        normalized_mode = (mode or "").strip().lower() or None
        # ADR-0098 V3: in pipeline mode against vLLM, translate
        # response_format into extra_body.guided_*. In agent mode the
        # Agents SDK tool-call structure carries shape; leave response_format
        # alone so we never JSON-force a tool-call response. For other
        # providers, response_format flows through unchanged in every mode.
        guided_kwargs: Optional[Dict[str, Any]] = None
        if (
            normalized_mode == "pipeline"
            and self.provider == "vllm"
            and response_format is not None
        ):
            guided_kwargs = self._translate_response_format_to_guided(response_format)

        if guided_kwargs is not None:
            # Guided decoding supersedes response_format on vLLM.
            kwargs["extra_body"] = self._merge_extra_body(
                kwargs.get("extra_body"),
                guided_kwargs,
            )
        elif response_format is not None:
            kwargs["response_format"] = response_format

        if "extra_body" in reasoning_overrides:
            kwargs["extra_body"] = self._merge_extra_body(
                kwargs.get("extra_body"),
                reasoning_overrides.pop("extra_body"),
            )
        kwargs.update(reasoning_overrides)
        return kwargs

    @staticmethod
    def _clone_completion_request_kwargs(kwargs: Dict[str, Any]) -> Dict[str, Any]:
        cloned = dict(kwargs)
        messages = cloned.get("messages")
        if isinstance(messages, list):
            cloned["messages"] = [dict(message) for message in messages]
        return cloned

    @staticmethod
    def _ensure_json_only_instruction(kwargs: Dict[str, Any]) -> None:
        messages = kwargs.get("messages")
        if not isinstance(messages, list) or not messages:
            return
        content = str(messages[0].get("content", ""))
        if "Return ONLY valid JSON." not in content:
            messages[0]["content"] = f"{content}\n\nReturn ONLY valid JSON."

    def _completion_retry_variants(self, kwargs: Dict[str, Any]) -> List[Dict[str, Any]]:
        variants = [self._clone_completion_request_kwargs(kwargs)]
        has_provider_overrides = any(
            key in kwargs for key in ("extra_body", "reasoning_effort")
        )

        if has_provider_overrides:
            stripped_overrides = self._clone_completion_request_kwargs(kwargs)
            stripped_overrides.pop("extra_body", None)
            stripped_overrides.pop("reasoning_effort", None)
            variants.append(stripped_overrides)

        if "response_format" in kwargs:
            json_prompt_variant = self._clone_completion_request_kwargs(kwargs)
            json_prompt_variant.pop("response_format", None)
            self._ensure_json_only_instruction(json_prompt_variant)
            variants.append(json_prompt_variant)

        if has_provider_overrides and "response_format" in kwargs:
            stripped_json_prompt = self._clone_completion_request_kwargs(kwargs)
            stripped_json_prompt.pop("extra_body", None)
            stripped_json_prompt.pop("reasoning_effort", None)
            stripped_json_prompt.pop("response_format", None)
            self._ensure_json_only_instruction(stripped_json_prompt)
            variants.append(stripped_json_prompt)

        return variants

    def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, Any]] = None,
        reasoning_mode: Optional[bool] = None,
        task_hint: Optional[str] = None,
        mode: Optional[str] = None,
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
            reasoning_mode=reasoning_mode,
            task_hint=task_hint,
            mode=mode,
        )
        last_exc: Optional[Exception] = None
        for attempt_kwargs in self._completion_retry_variants(kwargs):
            try:
                resp = self._client.chat.completions.create(**attempt_kwargs)
                return self._build_response(resp)
            except Exception as exc:
                last_exc = exc
        assert last_exc is not None
        raise last_exc

    async def acomplete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, Any]] = None,
        reasoning_mode: Optional[bool] = None,
        task_hint: Optional[str] = None,
        mode: Optional[str] = None,
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
            reasoning_mode=reasoning_mode,
            task_hint=task_hint,
            mode=mode,
        )
        last_exc: Optional[Exception] = None
        for attempt_kwargs in self._completion_retry_variants(kwargs):
            try:
                resp = await self._async_client.chat.completions.create(**attempt_kwargs)
                return self._build_response(resp)
            except Exception as exc:
                last_exc = exc
        assert last_exc is not None
        raise last_exc

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


class MaraBackend(OpenAICompatibleBackend):
    """MARA cloud provider — OpenAI-compatible (MiniMax-class models)."""

    def __init__(
        self,
        *,
        model: str = "MiniMax-M2.5",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(
            provider="mara",
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )


class VLLMBackend(OpenAICompatibleBackend):
    """ADR-0098: on-prem vLLM provider.

    Mirrors the OpenAI-compatible HTTP chat-completions API surfaced by
    vLLM's ``vllm.entrypoints.openai.api_server``. vLLM runs
    unauthenticated by default; if no API key is configured via the
    ``SEOCHO_VLLM_API_KEY`` (or legacy ``VLLM_API_KEY``) env var, the
    backend passes the documented ``"EMPTY"`` sentinel so the OpenAI
    client doesn't refuse to send the request.

    ``model`` is required (no sensible default — operators pick the
    served model, e.g. ``"Qwen2.5-7B-Instruct"``).
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
    ) -> None:
        # vLLM convention: pass "EMPTY" when no key is needed so the
        # OpenAI client's hard requirement of a non-empty api_key is met.
        resolved_key = api_key if (api_key and str(api_key).strip()) else None
        if resolved_key is None:
            # Look up env first so explicit user config wins; only fall
            # back to "EMPTY" when truly nothing is set.
            for env_name in ("SEOCHO_VLLM_API_KEY", "VLLM_API_KEY"):
                if os.getenv(env_name):
                    resolved_key = os.getenv(env_name)
                    break
        if resolved_key is None:
            resolved_key = "EMPTY"
        super().__init__(
            provider="vllm",
            model=model,
            api_key=resolved_key,
            base_url=base_url,
            timeout=timeout,
        )


def create_llm_backend(
    *,
    provider: str = "openai",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: Optional[float] = None,
) -> OpenAICompatibleBackend:
    """Create an OpenAI-compatible LLM backend by provider preset.

    When ``timeout`` is None the provider preset's ``default_timeout`` is used,
    so reasoning-model presets (kimi, grok) get more headroom than the 120s
    baseline (#118). Pass an explicit timeout to override.
    """

    provider_key = str(provider).strip().lower() or "openai"
    if timeout is None:
        try:
            timeout = get_provider_spec(provider_key).default_timeout
        except ValueError:
            timeout = 120.0
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
    if provider_key == "mara":
        return MaraBackend(
            model=model or get_provider_spec("mara").default_model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
    if provider_key == "vllm":
        vllm_model = model or get_provider_spec("vllm").default_model
        if not vllm_model:
            raise ValueError(
                "vllm provider requires an explicit model — vLLM's served "
                "model name is operator-chosen (e.g. 'Qwen2.5-7B-Instruct')."
            )
        return VLLMBackend(
            model=vllm_model,
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
