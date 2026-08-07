"""LLM I/O helpers for benchmark scripts.

Centralizes:
  - Provider selection (Moonshot/Kimi default, OpenAI for embeddings)
  - Retry with exponential backoff + jitter on transient errors
  - httpx-style timeouts
  - JSON response_format enforcement for judge-style calls (with fallback)
  - Simple chat completion wrapper that joins a meta system prompt
"""
from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

_RECEIPT_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------

_DEFAULT_RETRY_STATUSES = (408, 425, 429, 500, 502, 503, 504)


def _status_of(exc: Exception) -> int | None:
    # openai.APIStatusError carries .status_code; httpx errors have .response
    status = getattr(exc, "status_code", None)
    if status:
        return int(status)
    resp = getattr(exc, "response", None)
    if resp is not None:
        return int(getattr(resp, "status_code", 0) or 0) or None
    return None


# Rate-limit (429) gets its own, more patient retry schedule because shared
# gateways like MARA (DeepSeek-V3.1 ≈ 1500 RPD) throttle for tens of seconds.
_RATE_LIMIT_BACKOFF = (10.0, 30.0, 60.0, 90.0, 120.0)


def with_retry(
    fn: Callable[[], Any],
    *,
    max_attempts: int = 3,
    backoff_seconds: Iterable[float] = (1.0, 4.0, 16.0),
    retry_on_status: Iterable[int] = _DEFAULT_RETRY_STATUSES,
    retry_on_exc_names: Iterable[str] = ("Timeout", "APIConnectionError", "ReadTimeout", "ConnectError"),
    label: str = "llm",
    verbose: bool = False,
    rate_limit_attempts: int = 6,
    rate_limit_backoff: Iterable[float] = _RATE_LIMIT_BACKOFF,
) -> Any:
    """Run ``fn()`` with retries on transient HTTP / network errors.

    Idempotent ``fn`` only. Two schedules:
      - generic transient (5xx/timeout/conn): ``max_attempts`` w/ ``backoff_seconds``
      - rate limit (429): ``rate_limit_attempts`` w/ the longer ``rate_limit_backoff``
        (MARA & other shared gateways throttle for tens of seconds).
    Adds ±25% jitter to each delay.
    """
    backoffs = list(backoff_seconds)
    rl_backoffs = list(rate_limit_backoff)
    statuses = set(retry_on_status)
    exc_names = tuple(retry_on_exc_names)
    last_exc: Exception | None = None
    generic_used = 0
    rl_used = 0
    while True:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            name = type(exc).__name__
            status = _status_of(exc)
            is_rate_limit = status == 429 or "RateLimit" in name
            is_generic = (status in statuses and status != 429) or any(n in name for n in exc_names)
            if is_rate_limit:
                rl_used += 1
                if rl_used >= rate_limit_attempts:
                    raise
                wait = rl_backoffs[min(rl_used - 1, len(rl_backoffs) - 1)]
            elif is_generic:
                generic_used += 1
                if generic_used >= max_attempts:
                    raise
                wait = backoffs[min(generic_used - 1, len(backoffs) - 1)]
            else:
                raise
            wait = wait * (0.75 + random.random() * 0.5)
            if verbose:
                kind = "429" if is_rate_limit else "transient"
                print(f"  [retry:{label}] {kind} ({name} status={status}); sleep {wait:.1f}s", flush=True)
            time.sleep(wait)


# ---------------------------------------------------------------------------
# OpenAI-compatible client builders
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LLMSpec:
    provider: str   # "kimi" | "openai" | "xai" | "deepseek"
    model: str
    base_url: str | None
    api_key_env: str
    forced_temperature: float | None = None  # provider-specific overrides (e.g. Kimi=1)
    supports_response_format_json: bool = False  # OpenAI supports json_object officially

    @property
    def llm_string(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass(frozen=True)
class LLMCallReceipt:
    """Portable usage receipt emitted by either supported benchmark transport."""

    transport: str
    provider: str
    model: str
    label: str
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    response_cost: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "transport": self.transport,
            "provider": self.provider,
            "model": self.model,
            "label": self.label,
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "response_cost": self.response_cost,
        }


class _LiteLLMCompletions:
    def __init__(
        self,
        *,
        spec: LLMSpec,
        timeout_s: float,
        completion_fn: Callable[..., Any] | None = None,
    ) -> None:
        self._spec = spec
        self._timeout_s = timeout_s
        self._completion_fn = completion_fn

    def create(self, **kwargs: Any) -> Any:
        completion_fn = self._completion_fn
        if completion_fn is None:
            try:
                from litellm import completion as completion_fn  # type: ignore
            except ImportError as exc:
                raise RuntimeError(
                    "LiteLLM transport requested but litellm is not installed; "
                    "install the development dependencies or `uv add litellm`"
                ) from exc

        api_key = os.environ.get(self._spec.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"{self._spec.api_key_env} not set for provider {self._spec.provider}"
            )
        model = str(kwargs.pop("model"))
        request: dict[str, Any] = {
            **kwargs,
            "model": f"openai/{model}",
            "api_key": api_key,
            "timeout": self._timeout_s,
            # Retry remains owned by with_retry so every transport uses the
            # same experiment policy and attempt accounting.
            "max_retries": 0,
        }
        if self._spec.base_url:
            request["api_base"] = self._spec.base_url
        return completion_fn(**request)


class _LiteLLMChat:
    def __init__(self, completions: _LiteLLMCompletions) -> None:
        self.completions = completions


class LiteLLMChatClient:
    """OpenAI-client-shaped adapter backed by LiteLLM's Python SDK."""

    transport = "litellm"

    def __init__(
        self,
        *,
        spec: LLMSpec,
        timeout_s: float,
        completion_fn: Callable[..., Any] | None = None,
    ) -> None:
        self.chat = _LiteLLMChat(
            _LiteLLMCompletions(
                spec=spec,
                timeout_s=timeout_s,
                completion_fn=completion_fn,
            )
        )


# OpenAI-compatible providers, all use the openai SDK with a base_url override.
_PROVIDER_PRESETS: dict[str, dict] = {
    "kimi": {
        "default_model": "kimi-k2.5",
        "base_url": "https://api.moonshot.ai/v1",
        "api_key_env": "MOONSHOT_API_KEY",
        "forced_temperature": 1.0,
        "supports_response_format_json": False,
    },
    "openai": {
        "default_model": "gpt-4o-mini",
        "base_url": None,
        "api_key_env": "OPENAI_API_KEY",
        "forced_temperature": None,
        "supports_response_format_json": True,
    },
    "xai": {
        "default_model": "grok-4-fast-non-reasoning",
        "base_url": "https://api.x.ai/v1",
        "api_key_env": "XAI_API_KEY",
        "forced_temperature": None,
        "supports_response_format_json": False,
    },
    "deepseek": {
        "default_model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "forced_temperature": None,
        "supports_response_format_json": True,  # deepseek docs document JSON Output mode
    },
    # MARA cloud gateway (OpenAI-compatible). Models: DeepSeek-V3.1,
    # MiniMax-M2.5, MiniMax-M2.7, gpt-oss-120b. Default generator + judge
    # per repo policy (CLAUDE.md §19 live-experiment default).
    "mara": {
        "default_model": "DeepSeek-V3.1",
        "base_url": "https://api.cloud.mara.com/v1",
        "api_key_env": "MARA_API_KEY",
        "forced_temperature": None,
        "supports_response_format_json": True,
    },
}

# Known MARA model ids (exact casing — gateway is case-sensitive).
MARA_MODELS = ("DeepSeek-V3.1", "MiniMax-M2.5", "MiniMax-M2.7", "gpt-oss-120b")


def parse_llm_spec(spec: str) -> LLMSpec:
    """Parse ``provider/model`` or default to Moonshot Kimi K2.5."""
    if not spec:
        provider, model = "kimi", ""
    elif "/" in spec:
        provider, model = spec.split("/", 1)
        provider = provider.strip().lower()
        model = model.strip()
    else:
        provider, model = "kimi", spec
    if provider not in _PROVIDER_PRESETS:
        raise ValueError(f"unknown LLM provider: {provider!r} (known: {sorted(_PROVIDER_PRESETS)})")
    preset = _PROVIDER_PRESETS[provider]
    return LLMSpec(
        provider=provider,
        model=model or preset["default_model"],
        base_url=preset["base_url"],
        api_key_env=preset["api_key_env"],
        forced_temperature=preset["forced_temperature"],
        supports_response_format_json=preset["supports_response_format_json"],
    )


def known_providers() -> list[str]:
    return sorted(_PROVIDER_PRESETS)


def make_chat_client(
    spec: LLMSpec,
    *,
    connect_s: float = 10.0,
    read_s: float = 120.0,
    total_s: float = 300.0,
    transport: str | None = None,
):
    """Build a chat client targeting the spec's provider.

    ``transport`` is ``openai`` or ``litellm``. When omitted, the
    ``SEOCHO_BENCH_LLM_TRANSPORT`` environment variable is consulted and then
    falls back to ``openai`` for historical benchmark reproducibility.
    """
    selected_transport = (
        transport or os.environ.get("SEOCHO_BENCH_LLM_TRANSPORT") or "openai"
    ).strip().lower()
    if selected_transport == "litellm":
        return LiteLLMChatClient(spec=spec, timeout_s=total_s)
    if selected_transport != "openai":
        raise ValueError(
            f"unknown LLM transport: {selected_transport!r} "
            "(known: ['litellm', 'openai'])"
        )

    from openai import OpenAI  # type: ignore
    import httpx  # type: ignore

    api_key = os.environ.get(spec.api_key_env)
    if not api_key:
        raise RuntimeError(f"{spec.api_key_env} not set for provider {spec.provider}")
    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "timeout": httpx.Timeout(timeout=total_s, connect=connect_s, read=read_s),
    }
    if spec.base_url:
        kwargs["base_url"] = spec.base_url
    return OpenAI(**kwargs)


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _receipt_from_response(
    response: Any,
    *,
    client: Any,
    spec: LLMSpec | None,
    model: str,
    label: str,
    latency_ms: float,
) -> LLMCallReceipt:
    usage = _value(response, "usage", {}) or {}
    hidden = _value(response, "_hidden_params", {}) or {}
    raw_cost = _value(hidden, "response_cost")
    return LLMCallReceipt(
        transport=str(getattr(client, "transport", "openai")),
        provider=spec.provider if spec else "unknown",
        model=model,
        label=label,
        latency_ms=round(latency_ms, 3),
        prompt_tokens=int(_value(usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(_value(usage, "completion_tokens", 0) or 0),
        total_tokens=int(_value(usage, "total_tokens", 0) or 0),
        response_cost=float(raw_cost) if raw_cost is not None else None,
    )


def _emit_receipt(
    receipt: LLMCallReceipt,
    receipt_sink: Callable[[LLMCallReceipt], None] | None,
) -> None:
    if receipt_sink is not None:
        receipt_sink(receipt)
    receipt_path = os.environ.get("SEOCHO_BENCH_LLM_RECEIPT_PATH", "").strip()
    if not receipt_path:
        return
    path = Path(receipt_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(receipt.as_dict(), sort_keys=True) + "\n"
    with _RECEIPT_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(payload)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def chat_complete(
    *,
    client,
    model: str,
    system: str,
    user: str,
    temperature: float = 1.0,
    max_tokens: int | None = None,
    response_format: dict | None = None,
    label: str = "chat",
    max_attempts: int = 3,
    verbose: bool = False,
    spec: LLMSpec | None = None,
    receipt_sink: Callable[[LLMCallReceipt], None] | None = None,
) -> str:
    # Provider-specific safety: Kimi enforces temperature=1; drop response_format
    # for providers that don't officially support it (to avoid 400s).
    if spec is not None:
        if spec.forced_temperature is not None:
            temperature = spec.forced_temperature
        if response_format is not None and not spec.supports_response_format_json:
            # Caller will detect missing JSON via lenient parse and retry inline.
            response_format = None
    """Send a chat-completion with retry + return the assistant string.

    ``response_format`` is passed through when set (use ``{"type":"json_object"}``
    for structured JSON output). Falls back to no response_format on errors.
    """
    def _call_with_rf():
        kwargs: dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format
        started = time.perf_counter()
        resp = client.chat.completions.create(**kwargs)
        _emit_receipt(
            _receipt_from_response(
                resp,
                client=client,
                spec=spec,
                model=model,
                label=label,
                latency_ms=(time.perf_counter() - started) * 1000,
            ),
            receipt_sink,
        )
        return resp.choices[0].message.content or ""

    def _call_without_rf():
        kwargs: dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system + ("\n\nReturn ONLY valid JSON." if response_format else "")},
                {"role": "user", "content": user},
            ],
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        started = time.perf_counter()
        resp = client.chat.completions.create(**kwargs)
        _emit_receipt(
            _receipt_from_response(
                resp,
                client=client,
                spec=spec,
                model=model,
                label=f"{label}-fallback",
                latency_ms=(time.perf_counter() - started) * 1000,
            ),
            receipt_sink,
        )
        return resp.choices[0].message.content or ""

    try:
        return with_retry(_call_with_rf, max_attempts=max_attempts, label=label, verbose=verbose)
    except Exception as exc:  # noqa: BLE001
        if response_format is None:
            raise
        if verbose:
            print(f"  [chat:{label}] response_format failed ({type(exc).__name__}); retrying without it + JSON-only system tail", flush=True)
        return with_retry(_call_without_rf, max_attempts=max_attempts, label=f"{label}-fallback", verbose=verbose)


# ---------------------------------------------------------------------------
# JSON parsing helpers (for judge-style outputs)
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")
_FENCE_OPEN = re.compile(r"^```(?:json)?\s*", re.IGNORECASE)
_FENCE_CLOSE = re.compile(r"\s*```$")


def parse_json_lenient(text: str) -> dict | None:
    """Best-effort JSON parser tolerating code fences and surrounding prose."""
    if not text:
        return None
    raw = text.strip()
    raw = _FENCE_OPEN.sub("", raw)
    raw = _FENCE_CLOSE.sub("", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try the first {...} block
        m = _JSON_BLOCK_RE.search(raw)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


JUDGE_SYSTEM = (
    "You are a strict financial QA grader. You will be given a question, a "
    "gold answer written by a financial domain expert, and a model's predicted "
    "answer. Score the prediction's correctness on a 0-10 integer scale:\n"
    "  10 — fully correct: matches gold numbers, units, periods, and conclusions\n"
    "  7-9 — mostly correct: minor omissions or rephrasings\n"
    "  4-6 — partially correct: some right facts but wrong numbers or missing key parts\n"
    "  1-3 — mostly wrong but shows topic understanding\n"
    "  0  — wrong or refuses to answer ('query empty', 'no data', 'cannot determine')\n"
    "Output strict JSON: {\"score\": <int>, \"rationale\": \"<one sentence>\"}"
)


def llm_judge(
    *,
    client,
    model: str,
    question: str,
    gold: str,
    prediction: str,
    temperature: float = 1.0,
    max_attempts: int = 3,
    verbose: bool = False,
    spec: LLMSpec | None = None,
) -> dict:
    """Run a JSON-scoring judge with response_format + retry + lenient parse.

    Pass ``spec`` to enable provider-specific safety (Kimi temperature=1,
    drop response_format for providers that don't support it). The default
    judge in callers is ``openai/gpt-4o-mini`` which supports json_object
    natively → parse err should be 0.
    """
    user = (
        f"Question:\n{question}\n\n"
        f"Gold answer:\n{gold}\n\n"
        f"Predicted answer:\n{prediction}\n\n"
        f"Return JSON only."
    )
    text = chat_complete(
        client=client,
        model=model,
        system=JUDGE_SYSTEM,
        user=user,
        temperature=temperature,
        response_format={"type": "json_object"},
        label="judge",
        max_attempts=max_attempts,
        verbose=verbose,
        spec=spec,
    )
    parsed = parse_json_lenient(text)
    if isinstance(parsed, dict) and "score" in parsed:
        return parsed
    return {"score": -1, "rationale": f"judge parse error: {(text or '')[:160]}"}
