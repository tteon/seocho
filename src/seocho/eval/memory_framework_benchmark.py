"""Provider-neutral contracts for comparative agent-memory qualification."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence

from .longitudinal_memory import LongitudinalEvent


class CapabilityStatus(str, Enum):
    NATIVE = "native"
    ADAPTER = "adapter"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class MemoryCapabilities:
    current_read: CapabilityStatus
    point_in_time_read: CapabilityStatus
    temporal_invalidation: CapabilityStatus
    graph_relations: CapabilityStatus
    idempotent_write: CapabilityStatus
    rollback_or_rebuild: CapabilityStatus
    provenance: CapabilityStatus

    def to_dict(self) -> dict[str, str]:
        return {
            name: value.value
            for name, value in (
                ("current_read", self.current_read),
                ("point_in_time_read", self.point_in_time_read),
                ("temporal_invalidation", self.temporal_invalidation),
                ("graph_relations", self.graph_relations),
                ("idempotent_write", self.idempotent_write),
                ("rollback_or_rebuild", self.rollback_or_rebuild),
                ("provenance", self.provenance),
            )
        }


@dataclass(frozen=True, slots=True)
class MemoryObservation:
    memory_id: str
    state: str
    sequence: int | None
    provenance_refs: tuple[str, ...] = ()
    related_refs: tuple[str, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict)


class MemoryBenchmarkAdapter(Protocol):
    """Smallest fair interface shared by SEOCHO and peer memory systems."""

    @property
    def framework(self) -> str: ...

    @property
    def capabilities(self) -> MemoryCapabilities: ...

    def reset(self) -> None: ...

    def add(self, event: LongitudinalEvent) -> bool:
        """Return True for a new write and False for an idempotent replay."""

    def get_current(self, memory_id: str) -> MemoryObservation | None: ...

    def get_at_sequence(
        self, memory_id: str, sequence: int
    ) -> MemoryObservation | None: ...

    def search(self, query: str, *, limit: int) -> Sequence[MemoryObservation]: ...


@dataclass(frozen=True, slots=True)
class QualificationCase:
    case_id: str
    operation: str
    memory_id: str
    expected_state: str | None
    at_sequence: int | None = None


def build_temporal_cases(
    events: Sequence[LongitudinalEvent], *, sample_memories: int = 100
) -> tuple[QualificationCase, ...]:
    if sample_memories < 1:
        raise ValueError("sample_memories must be positive")
    histories: dict[str, list[LongitudinalEvent]] = {}
    for event in events:
        histories.setdefault(event.transaction_ref, []).append(event)
    cases: list[QualificationCase] = []
    for memory_id in sorted(histories)[:sample_memories]:
        history = sorted(histories[memory_id], key=lambda event: event.sequence)
        current = history[-1]
        cases.append(
            QualificationCase(
                case_id=f"current:{memory_id}",
                operation="current",
                memory_id=memory_id,
                expected_state=current.state,
            )
        )
        if len(history) > 1:
            prior = history[-2]
            cases.append(
                QualificationCase(
                    case_id=f"historical:{memory_id}:{prior.sequence}",
                    operation="point_in_time",
                    memory_id=memory_id,
                    expected_state=prior.state,
                    at_sequence=prior.sequence,
                )
            )
        if history[0].sequence > 1:
            cases.append(
                QualificationCase(
                    case_id=f"precreation:{memory_id}",
                    operation="point_in_time",
                    memory_id=memory_id,
                    expected_state=None,
                    at_sequence=history[0].sequence - 1,
                )
            )
    return tuple(cases)


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(round((len(ordered) - 1) * fraction), len(ordered) - 1)]


def qualify_adapter(
    adapter: MemoryBenchmarkAdapter,
    events: Sequence[LongitudinalEvent],
    cases: Sequence[QualificationCase],
) -> dict[str, Any]:
    """Ingest and score deterministic state semantics without an answer LLM."""

    adapter.reset()
    ingest_latencies: list[float] = []
    applied = 0
    started = time.perf_counter()
    for event in events:
        write_started = time.perf_counter()
        applied += int(adapter.add(event))
        ingest_latencies.append((time.perf_counter() - write_started) * 1000)
    ingest_elapsed = time.perf_counter() - started

    replay_started = time.perf_counter()
    replay_applied = adapter.add(events[-1]) if events else False
    replay_ms = (time.perf_counter() - replay_started) * 1000

    rows: list[dict[str, Any]] = []
    read_latencies: list[float] = []
    for case in cases:
        read_started = time.perf_counter()
        if case.operation == "current":
            observation = adapter.get_current(case.memory_id)
            support = adapter.capabilities.current_read
        else:
            support = adapter.capabilities.point_in_time_read
            observation = (
                None
                if support is CapabilityStatus.UNSUPPORTED
                else adapter.get_at_sequence(case.memory_id, int(case.at_sequence or 0))
            )
        latency_ms = (time.perf_counter() - read_started) * 1000
        read_latencies.append(latency_ms)
        observed_state = observation.state if observation else None
        rows.append(
            {
                "case_id": case.case_id,
                "operation": case.operation,
                "capability": support.value,
                "expected_state": case.expected_state,
                "observed_state": observed_state,
                "correct": (
                    None
                    if support is CapabilityStatus.UNSUPPORTED
                    else observed_state == case.expected_state
                ),
                "latency_ms": latency_ms,
                "provenance_count": (
                    len(observation.provenance_refs) if observation else 0
                ),
            }
        )
    scored = [row for row in rows if row["correct"] is not None]
    return {
        "schema_version": "seocho.memory-framework-qualification.v1",
        "framework": adapter.framework,
        "capabilities": adapter.capabilities.to_dict(),
        "events": len(events),
        "ingestion": {
            "applied": applied,
            "elapsed_seconds": ingest_elapsed,
            "events_per_second": len(events) / ingest_elapsed if events else 0.0,
            "p50_ms": _percentile(ingest_latencies, 0.50),
            "p95_ms": _percentile(ingest_latencies, 0.95),
        },
        "idempotent_replay": {
            "capability": adapter.capabilities.idempotent_write.value,
            "applied_twice": replay_applied,
            "latency_ms": replay_ms,
        },
        "retrieval": {
            "cases": len(rows),
            "scored_cases": len(scored),
            "correct": sum(row["correct"] is True for row in scored),
            "accuracy": (
                sum(row["correct"] is True for row in scored) / len(scored)
                if scored
                else None
            ),
            "p50_ms": _percentile(read_latencies, 0.50),
            "p95_ms": _percentile(read_latencies, 0.95),
        },
        "rows": rows,
    }


__all__ = [
    "CapabilityStatus",
    "MemoryBenchmarkAdapter",
    "MemoryCapabilities",
    "MemoryObservation",
    "QualificationCase",
    "build_temporal_cases",
    "qualify_adapter",
]
