"""Provider-neutral usage metrics for LiteLLM/OpenAI-compatible responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Mapping


@dataclass
class LLMUsage:
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None
    latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "latency_ms": round(self.latency_ms, 3),
            "metadata": dict(self.metadata),
        }


class UsageTimer:
    """Small context manager for attaching elapsed time to a usage record."""

    def __init__(self, usage: LLMUsage) -> None:
        self.usage = usage
        self._started = 0.0

    def __enter__(self) -> "UsageTimer":
        self._started = perf_counter()
        return self

    def __exit__(self, *_: object) -> None:
        self.usage.latency_ms = (perf_counter() - self._started) * 1000


def usage_from_response(
    response: Any, *, model: str = "", cost_usd: float | None = None
) -> LLMUsage:
    """Normalize dict/object LiteLLM responses without importing LiteLLM."""

    usage: Any = (
        response.get("usage", {})
        if isinstance(response, Mapping)
        else getattr(response, "usage", {})
    )
    if usage is None:
        usage = {}

    def read(name: str, fallback: int = 0) -> int:
        value = (
            usage.get(name, fallback)
            if isinstance(usage, Mapping)
            else getattr(usage, name, fallback)
        )
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return fallback

    total = read("total_tokens")
    input_tokens = read("prompt_tokens", read("input_tokens"))
    output_tokens = read("completion_tokens", read("output_tokens"))
    if not total:
        total = input_tokens + output_tokens
    resolved_model = model or (
        response.get("model", "")
        if isinstance(response, Mapping)
        else getattr(response, "model", "")
    )
    return LLMUsage(
        model=str(resolved_model or ""),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total,
        cost_usd=cost_usd,
    )
