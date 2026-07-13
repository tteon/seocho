"""Low-cardinality telemetry adapters for authoritative memory."""

from __future__ import annotations

from seocho.metrics import ProductionMetrics, get_metrics

_COMMIT_PHASES = frozenset(
    {
        "connection_scope",
        "idempotency_lookup",
        "aggregate_lock",
        "sequence_allocate",
        "revision_lookup",
        "memory_writes",
    }
)


class MemoryCommitMetricsObserver:
    """Export repository phase timings without tenant or transaction labels."""

    def __init__(self, metrics: ProductionMetrics | None = None) -> None:
        self._metrics = metrics

    def record(self, phase: str, elapsed_ms: float, outcome: str) -> None:
        if phase not in _COMMIT_PHASES:
            raise ValueError(f"unsupported memory commit phase: {phase}")
        if outcome not in {"ok", "error"}:
            raise ValueError(f"unsupported memory commit outcome: {outcome}")
        (self._metrics or get_metrics()).record(
            "seocho.memory.commit.phase.duration",
            elapsed_ms,
            {"phase": phase, "outcome": outcome},
        )


__all__ = ["MemoryCommitMetricsObserver"]
