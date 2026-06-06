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
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable


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


def with_retry(
    fn: Callable[[], Any],
    *,
    max_attempts: int = 3,
    backoff_seconds: Iterable[float] = (1.0, 4.0, 16.0),
    retry_on_status: Iterable[int] = _DEFAULT_RETRY_STATUSES,
    retry_on_exc_names: Iterable[str] = ("Timeout", "APIConnectionError", "ReadTimeout", "ConnectError"),
    label: str = "llm",
    verbose: bool = False,
) -> Any:
    """Run ``fn()`` with retries on transient HTTP / network errors.

    Idempotent ``fn`` only — the caller is responsible for ensuring the
    operation is safe to retry. Adds small jitter to each backoff delay.
    """
    backoffs = list(backoff_seconds)
    statuses = set(retry_on_status)
    exc_names = tuple(retry_on_exc_names)
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — broad on purpose; rethrow non-retriable below
            last_exc = exc
            name = type(exc).__name__
            status = _status_of(exc)
            retriable = (status in statuses) or any(n in name for n in exc_names)
            if not retriable or attempt >= max_attempts:
                raise
            wait = backoffs[min(attempt - 1, len(backoffs) - 1)]
            wait = wait * (0.75 + random.random() * 0.5)  # jitter ±25%
            if verbose:
                print(f"  [retry:{label}] attempt {attempt}/{max_attempts} failed ({name} status={status}); sleep {wait:.1f}s", flush=True)
            time.sleep(wait)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"with_retry({label}) exhausted without exception")


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
    # MARA cloud — OpenAI-compatible endpoint serving MiniMax-class models.
    # Mirrors src/seocho/store/llm.py's "mara" ProviderSpec (the SDK default).
    "mara": {
        "default_model": "MiniMax-M2.5",
        "base_url": "https://api.cloud.mara.com/v1",
        "api_key_env": "MARA_API_KEY",
        "forced_temperature": None,
        "supports_response_format_json": False,
    },
}


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


def make_chat_client(spec: LLMSpec, *, connect_s: float = 10.0, read_s: float = 120.0, total_s: float = 300.0):
    """Build an OpenAI SDK client targeting the spec's provider.

    Uses an httpx.Timeout if installed (it is, as a dep of openai sdk).
    """
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
        resp = client.chat.completions.create(**kwargs)
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
        resp = client.chat.completions.create(**kwargs)
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
