"""
LLM and embedding backend abstractions for the public SEOCHO SDK.

The default implementation uses OpenAI-compatible HTTP APIs so the same
interface can be reused across OpenAI, DeepSeek, Kimi, Grok, and Qwen.
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..tracing import capture_text, start_span
from ..metrics import get_metrics

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
    # override the 120s baseline: a single-document extraction routinely runs
    # far longer, and the timeout was tripping the heuristic fallback rather
    # than surfacing as an error, so the run reported success on manufactured
    # Entity/MENTIONS structure.
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
        # kimi-k2.5 single-document extraction was measured at 160-1450s; the
        # 120s baseline cut it off and silently degraded to heuristics.
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
        # Default model is a reasoning preset — same headroom.
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
        # MiniMax-M2.x is a reasoning model and mara is the default provider
        # for this repo, so it needs the headroom most. Added here rather than
        # inherited: the preset postdates the kimi/grok ones.
        default_timeout=900.0,
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


_EMBED_MAX_BATCH = 2048  # OpenAI-compatible /embeddings cap inputs at ~2048 per request


def _embed_in_batches(client: Any, model: str, texts: Sequence[str]) -> List[List[float]]:
    """Embed ``texts`` in provider-safe sub-batches and concatenate, preserving order.

    A single request for an arbitrarily large input hits the provider's per-request
    item cap and 400s, losing the whole batch; chunking degrades gracefully instead.
    Results are reordered by the response ``index`` so concatenation stays aligned.
    """
    items = list(texts)
    out: List[List[float]] = []
    for start in range(0, len(items), _EMBED_MAX_BATCH):
        batch = items[start : start + _EMBED_MAX_BATCH]
        if not batch:
            continue
        response = client.embeddings.create(model=model, input=batch)
        ordered = sorted(response.data, key=lambda item: item.index)
        out.extend(list(item.embedding) for item in ordered)
    return out


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
    if not resolved_api_key:
        from ..exceptions import SeochoCredentialError

        env_names = " or ".join((spec.api_key_env, *spec.api_key_env_aliases))
        raise SeochoCredentialError(
            f"No API key found for LLM provider '{spec.name}'. "
            f"Pass api_key=... (e.g. Seocho.local(ontology, api_key=...)) "
            f"or set the {env_names} environment variable. "
            f'For a local gateway that needs no key, pass api_key="EMPTY".'
        )
    kwargs: Dict[str, Any] = {"timeout": timeout}
    if resolved_api_key:
        kwargs["api_key"] = resolved_api_key
    if resolved_base_url:
        kwargs["base_url"] = resolved_base_url
    return spec, kwargs, resolved_api_key, resolved_base_url


@dataclass(slots=True)
class LLMResponse:
    """Structured response from an LLM call."""

    text: str
    model: str = ""
    usage: Dict[str, int] = field(default_factory=dict)

    def json(self) -> Any:
        """Parse JSON from plain, fenced, or reasoning-prefixed model output."""
        text = self.text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            text = "\n".join(lines)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Reasoning models may mention an invalid or illustrative object
            # before emitting the final payload. Reuse the provider-aware
            # parser that examines every balanced object and selects the
            # largest valid one instead of failing on the first ``{...}``.
            from ..llm_structured import extract_json_object
            from ..metrics import get_metrics

            # Every trip through the salvage parser is a repair event: the
            # SeochoLLMRepairRegression alert watches this rate as the early
            # signal that a provider/model stopped honouring structured output.
            get_metrics().add(
                "seocho.gen_ai.structured_output_repair.count",
                attributes={
                    "gen_ai.request.model": self.model or "unknown",
                    "reason": "non_json_text_salvage",
                },
            )
            return extract_json_object(text)


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
        model: Optional[str] = None,
        provider_options: Optional[Dict[str, Any]] = None,
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
        model: Optional[str] = None,
        provider_options: Optional[Dict[str, Any]] = None,
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


# seocho-jdg: task_hint -> ModelRouter task. Only mapped hints are routed;
# unmapped hints conservatively keep the backend's bound model.
_TASK_HINT_TO_ROUTER_TASK: Dict[str, str] = {
    "json_extraction": "extract",
    "entity_linking": "link",
    "answer_synthesis": "synthesize",
}


def _env_routed_model(llm: Any, task_hint: Optional[str]) -> Optional[str]:
    """Cost-aware model for this call, or None (the default: routing OFF).

    'Route on a known signal': every live LLM call funnels through
    complete_with_task_hints carrying a task_hint, so this single chokepoint
    covers extraction, linking, and answer synthesis without per-caller wiring.

    Guards (all must hold, else None — bound model unchanged):
    - SEOCHO_MODEL_ROUTING is truthy (explicit opt-in),
    - the task_hint maps to a router task,
    - the backend provider is 'mara' (the default tier map names MARA-hosted
      models; routing across provider families would 404). Other providers can
      opt in by overriding tiers via SEOCHO_MODEL_ROUTING_TIERS, e.g.
      "FAST=gpt-4o-mini,BALANCED=gpt-4o,FRONTIER=gpt-4o" — the provider guard
      is skipped when explicit tiers are supplied.
    """
    if os.getenv("SEOCHO_MODEL_ROUTING", "").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    task = _TASK_HINT_TO_ROUTER_TASK.get((task_hint or "").strip().lower())
    if not task:
        return None
    from ..routing import ModelRouter, ModelTier  # lazy: avoid import cycles

    tiers_env = os.getenv("SEOCHO_MODEL_ROUTING_TIERS", "").strip()
    if tiers_env:
        tier_models = {}
        for part in tiers_env.split(","):
            name, _, model_id = part.partition("=")
            try:
                tier_models[ModelTier[name.strip().upper()]] = model_id.strip()
            except KeyError:
                continue
        if not tier_models:
            return None
        router = ModelRouter(tier_models=tier_models)
    else:
        if getattr(llm, "provider", "") != "mara":
            return None
        router = ModelRouter.mara_default()
    return router.route(task=task).model


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
    model: Optional[str] = None,
    provider_options: Optional[Dict[str, Any]] = None,
) -> Any:
    """Call ``llm.complete`` while remaining compatible with older test doubles.

    ``model`` (seocho-jdg) is an optional per-call override of the backend's
    bound model — the primitive that lets a cost-aware router send this single
    request to a cheaper/stronger tier without rebuilding the client. ``None``
    (default) leaves the backend's configured model untouched.
    """

    kwargs: Dict[str, Any] = {
        "system": system,
        "user": user,
        "temperature": temperature,
    }
    if max_tokens is None and _strip_text(task_hint).lower() in (
        "json_extraction",
        "json_extraction_retry",
        "entity_linking",
    ):
        # Structured-output calls on reasoning models (MARA MiniMax-M2.7,
        # DeepSeek) spend thousands of completion tokens thinking before the
        # JSON payload. Leaving max_tokens to the server default truncates
        # mid-reasoning — the response carries reasoning text and no JSON,
        # or the provider 400s ("token limit reached before a complete JSON
        # object"). Give these calls an explicit generous budget (seocho-ub5).
        max_tokens = 8192
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
    if model is None:
        # opt-in cost-aware routing on the task_hint signal (seocho-jdg);
        # an explicit model= from the caller always wins over the router.
        model = _env_routed_model(llm, task_hint)
    if model is not None:
        kwargs["model"] = model
    if provider_options:
        kwargs["provider_options"] = provider_options
    # Older backends predate some optional kwargs (mode/reasoning_mode/
    # task_hint/model/provider_options, and the task-hint max_tokens
    # default). Strip ONLY the kwarg each TypeError names, so a double that
    # accepts reasoning_mode but not max_tokens keeps every kwarg it
    # understands.
    import re as _re

    optional_kwargs = (
        "reasoning_mode", "task_hint", "mode", "model",
        "provider_options", "max_tokens",
    )
    for _ in range(len(optional_kwargs) + 1):
        try:
            return llm.complete(**kwargs)
        except TypeError as exc:
            message = str(exc)
            if "unexpected keyword argument" not in message:
                raise
            match = _re.search(r"unexpected keyword argument '([^']*)'", message)
            offender = match.group(1) if match else None
            if offender in optional_kwargs and offender in kwargs:
                kwargs.pop(offender)
                continue
            # Unparseable message — legacy blanket strip as a last resort.
            for key in optional_kwargs:
                kwargs.pop(key, None)
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
        client = openai.OpenAI(**kwargs)
        async_client = openai.AsyncOpenAI(**kwargs)

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
        model: Optional[str] = None,
        provider_options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": model or self.model,
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
        # ADR-0098 translated response_format into extra_body.guided_* for
        # vLLM. That was correct for vLLM 0.4-era guided decoding and is now
        # actively harmful: `guided_json` does not exist in vLLM 0.27 (grep of
        # the installed package returns zero hits — the API is
        # `structured_outputs`), and OpenAIBaseModel is ConfigDict(extra="allow"),
        # so the unknown field is accepted and dropped with only a debug log.
        #
        # The translation was an `elif`, so when it fired `response_format` was
        # stripped from the request. vLLM handles `response_format` natively and
        # correctly — structured_outputs_from_response_format maps json_object
        # and json_schema itself, including the schema unwrap our translator got
        # wrong. So the net effect was to convert working structured output into
        # none at all, on exactly the self-hosted deployment we target.
        #
        # Three JSON safety nets keyed off `response_format` being present and
        # therefore also disabled themselves: the doubled-budget retry, the
        # "Return ONLY valid JSON" prompt variant, and the salvage-parser
        # accounting. Passing response_format straight through re-arms all of
        # them. Deleting the translation is strictly better than fixing it.
        if response_format is not None:
            kwargs["response_format"] = response_format

        if "extra_body" in reasoning_overrides:
            kwargs["extra_body"] = self._merge_extra_body(
                kwargs.get("extra_body"),
                reasoning_overrides.pop("extra_body"),
            )
        kwargs.update(reasoning_overrides)
        if provider_options:
            allowed = {"prompt_cache_key", "cache_salt", "thinking"}
            unknown = sorted(set(provider_options) - allowed)
            if unknown:
                raise ValueError(
                    "Unsupported provider options: " + ", ".join(unknown)
                )
            kwargs["extra_body"] = self._merge_extra_body(
                kwargs.get("extra_body"), provider_options
            )
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

    # Provider error text signalling "payload fine, the model ran out of
    # budget before completing the constrained JSON" (MARA wording).
    _JSON_GENERATION_FAILURE_MARKERS = (
        "did not output valid json",
        "truncated before a complete json",
    )

    @classmethod
    def _maybe_boost_json_budget(
        cls,
        *,
        exc: Exception,
        attempt_kwargs: Dict[str, Any],
        variants: List[Dict[str, Any]],
        position: int,
    ) -> bool:
        """Insert a same-shape, doubled-budget retry for JSON-truncation 400s.

        These errors are generation failures, not payload rejections: falling
        straight down the strip ladder removes ``response_format``, and the
        unconstrained retry produces runaway thinking-in-content prose with no
        JSON at all (observed 43k-char responses on MARA MiniMax-M2.7). Retry
        the SAME request shape with more budget first; the strip ladder stays
        as the final fallback. Returns True if a variant was inserted."""
        if "response_format" not in attempt_kwargs:
            return False
        message = str(exc).lower()
        if not any(m in message for m in cls._JSON_GENERATION_FAILURE_MARKERS):
            return False
        boosted = cls._clone_completion_request_kwargs(attempt_kwargs)
        boosted["max_tokens"] = max(
            int(attempt_kwargs.get("max_tokens") or 0) * 2, 16384
        )
        variants.insert(position, boosted)
        return True

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

    def _completion_trace_payload(
        self,
        *,
        system: str,
        user: str,
        temperature: float,
        task_hint: Optional[str],
        mode: Optional[str],
        model: Optional[str],
    ) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        captured_system = capture_text(system)
        captured_user = capture_text(user)
        input_data: Dict[str, Any] = {}
        if captured_system is not None:
            input_data["gen_ai.prompt.system"] = captured_system
        if captured_user is not None:
            input_data["gen_ai.prompt.user"] = captured_user
        metadata = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": self.provider,
            "gen_ai.request.model": model or self.model,
            "gen_ai.request.temperature": float(temperature),
            "seocho.prompt.system_hash": hashlib.sha256(system.encode("utf-8")).hexdigest()[:16],
            "seocho.prompt.user_hash": hashlib.sha256(user.encode("utf-8")).hexdigest()[:16],
            "seocho.prompt.system_chars": len(system),
            "seocho.prompt.user_chars": len(user),
            "seocho.task_hint": str(task_hint or ""),
            "seocho.llm.mode": str(mode or ""),
        }
        return input_data or None, metadata

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
        model: Optional[str] = None,
        provider_options: Optional[Dict[str, Any]] = None,
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
            model=model,
            provider_options=provider_options,
        )
        trace_input, trace_metadata = self._completion_trace_payload(
            system=system,
            user=user,
            temperature=temperature,
            task_hint=task_hint,
            mode=mode,
            model=model,
        )
        if provider_options:
            trace_metadata["seocho.prompt.provider_option_keys"] = sorted(provider_options)
            cache_identity = str(
                provider_options.get("prompt_cache_key")
                or provider_options.get("cache_salt")
                or ""
            )
            if cache_identity:
                trace_metadata["seocho.prompt.cache_identity_hash"] = hashlib.sha256(
                    cache_identity.encode("utf-8")
                ).hexdigest()[:16]
        metric_started = time.perf_counter()
        metrics = get_metrics()
        resolved_model = model or self.model
        variants = self._completion_retry_variants(kwargs)
        with start_span(
            "gen_ai.chat",
            input_data=trace_input,
            metadata=trace_metadata,
            tags=["gen_ai", f"provider:{self.provider}"],
        ) as span:
            last_exc: Optional[Exception] = None
            budget_boosted = False
            for attempt, attempt_kwargs in enumerate(variants, start=1):
                try:
                    resp = self._client.chat.completions.create(**attempt_kwargs)
                    result = self._build_response(resp)
                    span.set_output(
                        **{
                            "gen_ai.response.model": result.model,
                            "gen_ai.usage.input_tokens": result.usage.get("prompt_tokens", 0),
                            "gen_ai.usage.output_tokens": result.usage.get("completion_tokens", 0),
                            "gen_ai.usage.total_tokens": result.usage.get("total_tokens", 0),
                            "gen_ai.usage.cached_input_tokens": result.usage.get("cached_tokens", 0),
                            "seocho.llm.attempt_count": attempt,
                        }
                    )
                    completion = capture_text(result.text)
                    if completion is not None:
                        span.set_output(**{"gen_ai.completion": completion})
                    metric_labels = {
                        "gen_ai.provider.name": self.provider,
                        "gen_ai.request.model": resolved_model,
                    }
                    metrics.record(
                        "gen_ai.client.operation.duration",
                        time.perf_counter() - metric_started,
                        {**metric_labels, "gen_ai.operation.name": "chat"},
                    )
                    for token_type, usage_key in (
                        ("input", "prompt_tokens"),
                        ("output", "completion_tokens"),
                        ("cached_input", "cached_tokens"),
                    ):
                        usage = int(result.usage.get(usage_key, 0) or 0)
                        if usage:
                            metrics.record(
                                "gen_ai.client.token.usage",
                                usage,
                                {**metric_labels, "gen_ai.token.type": token_type},
                            )
                    metrics.add(
                        "seocho.gen_ai.prompt_cache.request.count",
                        attributes={
                            **metric_labels,
                            "outcome": (
                                "hit" if result.usage.get("cached_tokens", 0) else "miss_or_unreported"
                            ),
                        },
                    )
                    return result
                except Exception as exc:
                    last_exc = exc
                    if not budget_boosted:
                        # list.insert during iteration is safe here: the
                        # boosted variant lands at the position the loop
                        # visits next.
                        budget_boosted = self._maybe_boost_json_budget(
                            exc=exc,
                            attempt_kwargs=attempt_kwargs,
                            variants=variants,
                            position=attempt,
                        )
                    if attempt < len(variants):
                        metrics.add(
                            "seocho.gen_ai.retry.count",
                            attributes={
                                "gen_ai.provider.name": self.provider,
                                "gen_ai.request.model": resolved_model,
                                "reason": type(exc).__name__,
                            },
                        )
            assert last_exc is not None
            metrics.record(
                "gen_ai.client.operation.duration",
                time.perf_counter() - metric_started,
                {
                    "gen_ai.provider.name": self.provider,
                    "gen_ai.request.model": resolved_model,
                    "gen_ai.operation.name": "chat",
                    "error.type": type(last_exc).__name__,
                },
            )
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
        model: Optional[str] = None,
        provider_options: Optional[Dict[str, Any]] = None,
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
            model=model,
            provider_options=provider_options,
        )
        trace_input, trace_metadata = self._completion_trace_payload(
            system=system,
            user=user,
            temperature=temperature,
            task_hint=task_hint,
            mode=mode,
            model=model,
        )
        if provider_options:
            trace_metadata["seocho.prompt.provider_option_keys"] = sorted(provider_options)
            cache_identity = str(
                provider_options.get("prompt_cache_key")
                or provider_options.get("cache_salt")
                or ""
            )
            if cache_identity:
                trace_metadata["seocho.prompt.cache_identity_hash"] = hashlib.sha256(
                    cache_identity.encode("utf-8")
                ).hexdigest()[:16]
        metric_started = time.perf_counter()
        metrics = get_metrics()
        resolved_model = model or self.model
        variants = self._completion_retry_variants(kwargs)
        with start_span(
            "gen_ai.chat",
            input_data=trace_input,
            metadata=trace_metadata,
            tags=["gen_ai", f"provider:{self.provider}"],
        ) as span:
            last_exc: Optional[Exception] = None
            budget_boosted = False
            for attempt, attempt_kwargs in enumerate(variants, start=1):
                try:
                    resp = await self._async_client.chat.completions.create(**attempt_kwargs)
                    result = self._build_response(resp)
                    span.set_output(
                        **{
                            "gen_ai.response.model": result.model,
                            "gen_ai.usage.input_tokens": result.usage.get("prompt_tokens", 0),
                            "gen_ai.usage.output_tokens": result.usage.get("completion_tokens", 0),
                            "gen_ai.usage.total_tokens": result.usage.get("total_tokens", 0),
                            "gen_ai.usage.cached_input_tokens": result.usage.get("cached_tokens", 0),
                            "seocho.llm.attempt_count": attempt,
                        }
                    )
                    completion = capture_text(result.text)
                    if completion is not None:
                        span.set_output(**{"gen_ai.completion": completion})
                    metric_labels = {
                        "gen_ai.provider.name": self.provider,
                        "gen_ai.request.model": resolved_model,
                    }
                    metrics.record(
                        "gen_ai.client.operation.duration",
                        time.perf_counter() - metric_started,
                        {**metric_labels, "gen_ai.operation.name": "chat"},
                    )
                    for token_type, usage_key in (
                        ("input", "prompt_tokens"),
                        ("output", "completion_tokens"),
                        ("cached_input", "cached_tokens"),
                    ):
                        usage = int(result.usage.get(usage_key, 0) or 0)
                        if usage:
                            metrics.record(
                                "gen_ai.client.token.usage",
                                usage,
                                {**metric_labels, "gen_ai.token.type": token_type},
                            )
                    metrics.add(
                        "seocho.gen_ai.prompt_cache.request.count",
                        attributes={
                            **metric_labels,
                            "outcome": (
                                "hit" if result.usage.get("cached_tokens", 0) else "miss_or_unreported"
                            ),
                        },
                    )
                    return result
                except Exception as exc:
                    last_exc = exc
                    if not budget_boosted:
                        # list.insert during iteration is safe here: the
                        # boosted variant lands at the position the loop
                        # visits next.
                        budget_boosted = self._maybe_boost_json_budget(
                            exc=exc,
                            attempt_kwargs=attempt_kwargs,
                            variants=variants,
                            position=attempt,
                        )
                    if attempt < len(variants):
                        metrics.add(
                            "seocho.gen_ai.retry.count",
                            attributes={
                                "gen_ai.provider.name": self.provider,
                                "gen_ai.request.model": resolved_model,
                                "reason": type(exc).__name__,
                            },
                        )
            assert last_exc is not None
            metrics.record(
                "gen_ai.client.operation.duration",
                time.perf_counter() - metric_started,
                {
                    "gen_ai.provider.name": self.provider,
                    "gen_ai.request.model": resolved_model,
                    "gen_ai.operation.name": "chat",
                    "error.type": type(last_exc).__name__,
                },
            )
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
        return _embed_in_batches(self._client, resolved_model, texts)

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
            details = getattr(resp.usage, "prompt_tokens_details", None)
            cached_tokens = getattr(resp.usage, "cached_tokens", None)
            if cached_tokens is None and details is not None:
                if isinstance(details, dict):
                    cached_tokens = details.get("cached_tokens")
                else:
                    cached_tokens = getattr(details, "cached_tokens", None)
            usage = {
                "prompt_tokens": int(getattr(resp.usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(resp.usage, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(resp.usage, "total_tokens", 0) or 0),
                "cached_tokens": int(cached_tokens or 0),
            }
        # Reasoning models may return the answer in a reasoning field when
        # ``content`` is empty — typically when generation was cut short by
        # max_tokens. Field name varies by provider: ``reasoning_content``
        # (Kimi K2.5) vs ``reasoning`` (MARA MiniMax-M2.7). Unknown fields
        # land in the SDK model's ``model_extra``, so check both surfaces.
        text = getattr(choice.message, "content", "") or ""
        if not text:
            extra = getattr(choice.message, "model_extra", None) or {}
            for field_name in ("reasoning_content", "reasoning"):
                text = (
                    getattr(choice.message, field_name, "")
                    or extra.get(field_name)
                    or ""
                )
                if text:
                    break
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
        self._client = openai.OpenAI(**kwargs)

    def embed(
        self,
        texts: Sequence[str],
        *,
        model: Optional[str] = None,
    ) -> List[List[float]]:
        resolved_model = _strip_text(model) or self.model
        return _embed_in_batches(self._client, resolved_model, texts)

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

    When ``timeout`` is None the provider preset's ``default_timeout`` applies,
    so reasoning presets (mara, kimi, grok) get more headroom than the 120s
    baseline. Pass an explicit timeout to override.
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
