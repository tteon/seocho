"""Optional OpenTelemetry bridge for SDCR and LiteLLM runtime metadata.

The adapter is dependency-free at import time. When OTEL packages are absent,
it records no-op spans and never changes query behavior.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Mapping


class OTelBridge:
    """Emit low-cardinality metrics and redacted span attributes when enabled."""

    def __init__(self, *, tracer: Any = None, meter: Any = None) -> None:
        self.tracer = tracer
        self.meter = meter
        self._counters: dict[str, Any] = {}
        self._histograms: dict[str, Any] = {}
        if meter is not None:
            self._counters["agent_calls"] = meter.create_counter(
                "seocho_agent_calls_total"
            )
            self._counters["route"] = meter.create_counter("seocho_sdcr_route_total")
            self._counters["tokens"] = meter.create_counter("seocho_llm_tokens_total")
            self._histograms["latency"] = meter.create_histogram(
                "seocho_agent_latency_ms"
            )
            self._histograms["cost"] = meter.create_histogram("seocho_llm_cost_usd")

    @contextmanager
    def span(
        self, name: str, *, attributes: Mapping[str, Any] | None = None
    ) -> Iterator[Any]:
        if self.tracer is None:
            yield _NoopSpan()
            return
        with self.tracer.start_as_current_span(name) as current:
            for key, value in (attributes or {}).items():
                if value is not None:
                    current.set_attribute(str(key), _safe_value(value))
            yield current

    def record_route(self, reason: str, selected_count: int) -> None:
        self._add("route", 1, {"reason": reason, "selected_count": str(selected_count)})

    def record_usage(
        self,
        *,
        model: str,
        total_tokens: int,
        cost_usd: float | None,
        latency_ms: float,
    ) -> None:
        labels = {"model": model or "unknown"}
        self._add("tokens", max(0, int(total_tokens)), labels)
        self._record("latency", max(0.0, float(latency_ms)), labels)
        if cost_usd is not None:
            self._record("cost", max(0.0, float(cost_usd)), labels)

    def record_agent_call(self, agent_type: str) -> None:
        self._add("agent_calls", 1, {"agent_type": agent_type or "unknown"})

    def _add(self, name: str, value: int, attributes: Mapping[str, str]) -> None:
        instrument = self._counters.get(name)
        if instrument is not None:
            instrument.add(value, attributes=dict(attributes))

    def _record(self, name: str, value: float, attributes: Mapping[str, str]) -> None:
        instrument = self._histograms.get(name)
        if instrument is not None:
            instrument.record(value, attributes=dict(attributes))


class _NoopSpan:
    def set_attribute(self, _key: str, _value: Any) -> None:
        return None


def _safe_value(value: Any) -> str | bool | int | float:
    if isinstance(value, (str, bool, int, float)):
        return value
    return str(value)
