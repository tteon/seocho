"""Machine-readable scorecard contract for critical agent-memory incidents."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Tuple

from seocho.metrics import ProductionMetrics, get_metrics


CRITICAL_SCENARIO_IDS: tuple[str, ...] = tuple(f"S{i}" for i in range(1, 11))


@dataclass(frozen=True, slots=True)
class CriticalScenarioResult:
    scenario_id: str
    dataset_manifest: str
    service_versions: Mapping[str, str]
    concurrency: int
    memory_sequence: int
    projection_watermark: int
    support_status: str
    required_slots: Tuple[str, ...]
    missing_slots: Tuple[str, ...]
    provenance_coverage: float
    disclosure_violations: int
    latency_ms: Mapping[str, float]
    trace_id: str
    live_services: Tuple[str, ...]
    skipped_gates: Tuple[str, ...] = ()
    lost_commits: int = 0
    silent_stale_answers: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.scenario_id not in CRITICAL_SCENARIO_IDS:
            raise ValueError("unknown critical scenario")
        if self.concurrency < 1:
            raise ValueError("concurrency must be positive")
        if self.memory_sequence < 0 or self.projection_watermark < 0:
            raise ValueError("memory sequence and watermark must be non-negative")
        if self.support_status not in {"supported", "partial", "stale", "unsupported"}:
            raise ValueError("invalid support status")
        if not 0.0 <= self.provenance_coverage <= 1.0:
            raise ValueError("provenance coverage must be between zero and one")
        if self.disclosure_violations < 0 or self.lost_commits < 0:
            raise ValueError("violation and lost-commit counts must be non-negative")

    @property
    def passed_common_gates(self) -> bool:
        return (
            self.disclosure_violations == 0
            and self.lost_commits == 0
            and self.silent_stale_answers == 0
            and self.provenance_coverage == 1.0
            and not self.skipped_gates
        )

    @property
    def projection_current(self) -> bool:
        return self.projection_watermark >= self.memory_sequence

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["passed_common_gates"] = self.passed_common_gates
        payload["projection_current"] = self.projection_current
        return payload


def assert_live_evidence(
    result: CriticalScenarioResult, *, required_services: Tuple[str, ...]
) -> None:
    missing = sorted(set(required_services) - set(result.live_services))
    if missing:
        raise ValueError(
            "scenario lacks live evidence for required services: " + ", ".join(missing)
        )


def emit_critical_scenario_metrics(
    result: CriticalScenarioResult, *, metrics: ProductionMetrics | None = None
) -> None:
    """Emit bounded evaluation metrics after a scenario reaches a terminal state."""

    sink = metrics or get_metrics()
    labels = {
        "scenario_id": result.scenario_id,
        "support_status": result.support_status,
    }
    sink.add("seocho.critical.scenario.runs", attributes=labels)
    if result.passed_common_gates:
        sink.add(
            "seocho.critical.scenario.passed",
            attributes={"scenario_id": result.scenario_id},
        )
    sink.add(
        "seocho.critical.silent_stale",
        result.silent_stale_answers,
        {"scenario_id": result.scenario_id},
    )
    sink.add(
        "seocho.critical.disclosure_violations",
        result.disclosure_violations,
        {"scenario_id": result.scenario_id},
    )
    sink.set(
        "seocho.critical.memory_sequence",
        result.memory_sequence,
        {"scenario_id": result.scenario_id},
    )
    sink.set(
        "seocho.critical.projection_watermark",
        result.projection_watermark,
        {"scenario_id": result.scenario_id},
    )
    sink.set(
        "seocho.critical.projection_lag",
        max(result.memory_sequence - result.projection_watermark, 0),
        {"scenario_id": result.scenario_id},
    )
    sink.set("seocho.critical.scenario.info", 1, labels)
    for stage, latency_ms in result.latency_ms.items():
        sink.record(
            "seocho.critical.latency",
            latency_ms,
            {"scenario_id": result.scenario_id, "stage": stage},
        )


__all__ = [
    "CRITICAL_SCENARIO_IDS",
    "CriticalScenarioResult",
    "assert_live_evidence",
    "emit_critical_scenario_metrics",
]
