"""Unified 5-provider interface for the teaching notebooks.

Wraps ``seocho.create_llm_backend`` with notebook-friendly helpers:

    available_providers()           -> {provider: bool}
    make_backend(name)              -> OpenAICompatibleBackend
    chat(name, text, *, system=...) -> str
    compare_providers(user_prompt)  -> pandas.DataFrame

All 5 providers (Kimi / DeepSeek / OpenAI / Grok / Z.AI) share the same
OpenAI-compatible call shape; Kimi auto-clamps temperature=1.0 inside the
backend (no caller-side workaround needed).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional

import pandas as pd

from seocho import LLMResponse, create_llm_backend


PROVIDERS = {
    "kimi": {
        "provider": "kimi",
        "model": "kimi-k2.5",
        "key_env": "MOONSHOT_API_KEY",
        "tagline": "long-context Korean/Chinese instruction follower",
    },
    "deepseek": {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
        "tagline": "cost-efficient reasoning, JSON-mode reliable",
    },
    "openai": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "key_env": "OPENAI_API_KEY",
        "tagline": "broadest tool-use ecosystem, default baseline",
    },
    "grok": {
        "provider": "grok",
        "model": "grok-4.20-reasoning",
        "key_env": "XAI_API_KEY",
        "tagline": "fresh web-grounded answers, reasoning mode",
    },
    "zai": {
        "provider": "zai",
        "model": "glm-5.1",
        "key_env": "ZAI_API_KEY",
        "tagline": "Zhipu GLM global, cost-efficient bilingual reasoning",
    },
}

DEFAULT_SYSTEM = (
    "You are a careful assistant. Respond in Korean unless the user writes "
    "in English. Cite evidence when answering factual questions."
)


@dataclass
class ProviderResult:
    provider: str
    model: str
    response: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    error: Optional[str] = None


def available_providers() -> dict[str, bool]:
    """Return {provider_name: is_key_configured}."""
    return {name: bool(os.getenv(spec["key_env"])) for name, spec in PROVIDERS.items()}


def make_backend(name: str):
    """Construct a backend for the given provider preset."""
    if name not in PROVIDERS:
        raise KeyError(f"Unknown provider '{name}'. Known: {sorted(PROVIDERS)}")
    spec = PROVIDERS[name]
    return create_llm_backend(provider=spec["provider"], model=spec["model"])


def chat(
    name: str,
    user: str,
    *,
    system: str = DEFAULT_SYSTEM,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
) -> LLMResponse:
    """Single-shot completion; returns the raw LLMResponse for inspection."""
    backend = make_backend(name)
    return backend.complete(
        system=system,
        user=user,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def _run_one(
    name: str,
    user: str,
    *,
    system: str,
    temperature: float,
    max_tokens: Optional[int],
    response_format: Optional[dict],
) -> ProviderResult:
    spec = PROVIDERS[name]
    t0 = time.perf_counter()
    try:
        backend = make_backend(name)
        resp = backend.complete(
            system=system,
            user=user,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        elapsed = int((time.perf_counter() - t0) * 1000)
        usage = resp.usage or {}
        return ProviderResult(
            provider=name,
            model=resp.model or spec["model"],
            response=(resp.text or "").strip(),
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            total_tokens=int(usage.get("total_tokens", 0)),
            latency_ms=elapsed,
        )
    except Exception as exc:  # provider-specific failures (rate limit, auth, etc.)
        elapsed = int((time.perf_counter() - t0) * 1000)
        return ProviderResult(
            provider=name,
            model=spec["model"],
            response="",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            latency_ms=elapsed,
            error=f"{type(exc).__name__}: {exc}",
        )


def compare_providers(
    user: str,
    *,
    system: str = DEFAULT_SYSTEM,
    providers: Optional[Iterable[str]] = None,
    temperature: float = 0.2,
    max_tokens: Optional[int] = 512,
    response_format: Optional[dict] = None,
    truncate_response: int = 280,
) -> pd.DataFrame:
    """Run the same prompt across all *configured* providers.

    Skips providers whose API key env var is unset. Returns a DataFrame with
    ``provider, model, response, prompt_tokens, completion_tokens, total_tokens,
    latency_ms, error``.
    """
    targets: List[str] = list(providers) if providers else list(PROVIDERS)
    rows: List[ProviderResult] = []
    for name in targets:
        if not os.getenv(PROVIDERS[name]["key_env"]):
            continue
        rows.append(
            _run_one(
                name,
                user,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
        )

    if not rows:
        return pd.DataFrame(
            columns=[
                "provider",
                "model",
                "response",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "latency_ms",
                "error",
            ]
        )

    df = pd.DataFrame([r.__dict__ for r in rows])
    df["response"] = df["response"].str.slice(0, truncate_response)
    return df


def providers_overview() -> pd.DataFrame:
    """Cheat-sheet table: provider × model × key env × short tagline × configured?"""
    avail = available_providers()
    return pd.DataFrame(
        [
            {
                "provider": name,
                "model": spec["model"],
                "key_env": spec["key_env"],
                "configured": avail[name],
                "tagline": spec["tagline"],
            }
            for name, spec in PROVIDERS.items()
        ]
    )
