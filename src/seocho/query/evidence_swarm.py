"""Bounded SDCR coalition execution for typed evidence assembly.

The coordinator deliberately assembles one evidence bundle.  It does not ask
specialists to debate independent prose answers.  Retrieval specialists may be
plain database workers or adapters around an agent runtime.
"""

from __future__ import annotations

import contextvars
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

from ..metrics import ProductionMetrics, get_metrics
from ..tracing import start_span
from .sdcr import (
    Capability,
    DecisionReceipt,
    Evidence,
    SDCRRouter,
    filter_evidence,
    verify_conflicts,
)


@dataclass(frozen=True, slots=True)
class EvidenceSwarmRequest:
    """One workspace-scoped query and its explicit answer-slot contract."""

    workspace_id: str
    intent_id: str
    question: str
    required_slots: tuple[str, ...]
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        if not self.intent_id.strip():
            raise ValueError("intent_id is required")
        normalized = tuple(
            dict.fromkeys(
                str(slot).strip() for slot in self.required_slots if str(slot).strip()
            )
        )
        if not normalized:
            raise ValueError("required_slots must not be empty")
        object.__setattr__(self, "required_slots", normalized)


class EvidenceSpecialist(Protocol):
    """Minimal adapter implemented by a database worker or agent tool."""

    capability: Capability

    def retrieve(self, request: EvidenceSwarmRequest) -> Sequence[Evidence]: ...


@dataclass(frozen=True, slots=True)
class SpecialistRun:
    view_id: str
    outcome: str
    latency_ms: float
    evidence_count: int
    error_type: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "view_id": self.view_id,
            "outcome": self.outcome,
            "latency_ms": round(self.latency_ms, 3),
            "evidence_count": self.evidence_count,
            "error_type": self.error_type,
        }


@dataclass(frozen=True, slots=True)
class SwarmEvidenceBundle:
    intent_id: str
    required_slots: tuple[str, ...]
    slot_fills: Mapping[str, tuple[Any, ...]]
    missing_slots: tuple[str, ...]
    selected_evidence: tuple[Evidence, ...]
    protected_evidence_count: int
    conflict_verification: Mapping[str, Any]
    provenance: tuple[Mapping[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "required_slots": list(self.required_slots),
            "slot_fills": {
                slot: list(values) for slot, values in self.slot_fills.items()
            },
            "missing_slots": list(self.missing_slots),
            "selected_evidence": [
                {
                    "source_id": item.source_id,
                    "view_id": item.view_id,
                    "slot": item.slot,
                    "value": item.value,
                    "provenance": dict(item.provenance),
                }
                for item in self.selected_evidence
            ],
            "protected_evidence_count": self.protected_evidence_count,
            "conflict_verification": dict(self.conflict_verification),
            "provenance": [dict(item) for item in self.provenance],
        }


@dataclass(frozen=True, slots=True)
class EvidenceSwarmResult:
    status: str
    receipt: DecisionReceipt
    bundle: SwarmEvidenceBundle
    specialist_runs: tuple[SpecialistRun, ...]
    answer: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "receipt": self.receipt.as_dict(),
            "bundle": self.bundle.as_dict(),
            "specialist_runs": [item.as_dict() for item in self.specialist_runs],
            "answer": self.answer,
        }


EvidenceSynthesizer = Callable[[EvidenceSwarmRequest, SwarmEvidenceBundle], str]


class EvidenceSwarmCoordinator:
    """Route and execute the smallest authorized evidence coalition."""

    def __init__(
        self,
        specialists: Sequence[EvidenceSpecialist],
        *,
        max_workers: int = 8,
        timeout_seconds: float = 5.0,
        metrics: ProductionMetrics | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._specialists = {
            specialist.capability.view_id: specialist for specialist in specialists
        }
        if len(self._specialists) != len(specialists):
            raise ValueError("specialist view_id values must be unique")
        self._max_workers = max_workers
        self._timeout_seconds = timeout_seconds
        self._metrics = metrics or get_metrics()

    def run(
        self,
        request: EvidenceSwarmRequest,
        *,
        synthesizer: EvidenceSynthesizer | None = None,
        conflicts: Sequence[str] = (),
    ) -> EvidenceSwarmResult:
        started = time.perf_counter()
        operation = "sdcr_evidence_swarm"
        self._metrics.add("seocho.agent.request.inflight", 1, {"operation": operation})
        outcome = "error"
        error_type = ""
        with start_span(
            "query.evidence_swarm",
            metadata={
                "workspace_id": request.workspace_id,
                "seocho.query.intent_id": request.intent_id,
                "seocho.query.required_slot_count": len(request.required_slots),
            },
            tags=["query", "multi-agent", "sdcr"],
        ) as span:
            try:
                receipt = SDCRRouter().route(
                    workspace_id=request.workspace_id,
                    required_slots=request.required_slots,
                    capabilities=(
                        specialist.capability
                        for specialist in self._specialists.values()
                    ),
                    conflicts=conflicts,
                )
                evidence, runs = self._execute_selected(request, receipt.selected_views)
                bundle = self._bundle(request, evidence)
                failed = any(run.outcome != "ok" for run in runs)
                conflicted = bundle.conflict_verification.get("status") == "conflict"
                partial = bool(bundle.missing_slots or failed or conflicted)
                status = "partial" if partial else "complete"
                answer = (
                    synthesizer(request, bundle)
                    if synthesizer is not None
                    else _deterministic_answer(bundle)
                )
                outcome = status
                if partial:
                    reason = (
                        "conflict"
                        if conflicted
                        else "specialist_failure"
                        if failed
                        else "missing_slots"
                    )
                    self._metrics.add(
                        "seocho.agent.partial.count",
                        1,
                        {"operation": operation, "reason": reason},
                    )
                span.set_output(
                    {
                        "status": status,
                        "selected_agent_count": len(receipt.selected_views),
                        "safe_evidence_count": len(bundle.selected_evidence),
                        "missing_slot_count": len(bundle.missing_slots),
                        "protected_evidence_count": bundle.protected_evidence_count,
                        "conflict_count": len(
                            bundle.conflict_verification.get("conflicts", [])
                        ),
                    }
                )
                return EvidenceSwarmResult(
                    status=status,
                    receipt=receipt,
                    bundle=bundle,
                    specialist_runs=runs,
                    answer=answer,
                )
            except Exception as exc:
                error_type = type(exc).__name__
                raise
            finally:
                duration_seconds = time.perf_counter() - started
                self._metrics.add(
                    "seocho.agent.request.inflight", -1, {"operation": operation}
                )
                self._metrics.add(
                    "seocho.agent.request.count",
                    1,
                    {"operation": operation, "outcome": outcome},
                )
                self._metrics.record(
                    "seocho.agent.request.duration",
                    duration_seconds,
                    {
                        "operation": operation,
                        "outcome": outcome,
                        "error.type": error_type,
                    },
                )

    def _execute_selected(
        self, request: EvidenceSwarmRequest, selected_views: Sequence[str]
    ) -> tuple[tuple[Evidence, ...], tuple[SpecialistRun, ...]]:
        if not selected_views:
            return (), ()
        executor = ThreadPoolExecutor(
            max_workers=min(self._max_workers, len(selected_views)),
            thread_name_prefix="seocho-sdcr",
        )
        future_views: dict[Future[tuple[tuple[Evidence, ...], SpecialistRun]], str] = {}
        try:
            for view_id in selected_views:
                specialist = self._specialists[view_id]
                context = contextvars.copy_context()
                future = executor.submit(
                    context.run, self._execute_one, specialist, request
                )
                future_views[future] = view_id
            done, pending = wait(future_views, timeout=self._timeout_seconds)
            evidence: list[Evidence] = []
            runs: list[SpecialistRun] = []
            for future in done:
                items, run = future.result()
                evidence.extend(items)
                runs.append(run)
            for future in pending:
                future.cancel()
                runs.append(
                    SpecialistRun(
                        view_id=future_views[future],
                        outcome="timeout",
                        latency_ms=self._timeout_seconds * 1000,
                        evidence_count=0,
                        error_type="TimeoutError",
                    )
                )
            return (
                tuple(
                    sorted(
                        evidence,
                        key=lambda item: (item.slot, item.view_id, item.source_id),
                    )
                ),
                tuple(sorted(runs, key=lambda item: item.view_id)),
            )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _execute_one(
        self, specialist: EvidenceSpecialist, request: EvidenceSwarmRequest
    ) -> tuple[tuple[Evidence, ...], SpecialistRun]:
        started = time.perf_counter()
        view_id = specialist.capability.view_id
        outcome = "error"
        error_type = ""
        items: tuple[Evidence, ...] = ()
        with start_span(
            "query.evidence_swarm.specialist",
            metadata={
                "workspace_id": request.workspace_id,
                "seocho.agent.type": view_id,
            },
            tags=["query", "specialist"],
        ) as span:
            try:
                items = tuple(specialist.retrieve(request))
                undeclared = sorted(
                    {
                        item.slot
                        for item in items
                        if item.slot not in specialist.capability.slots
                    }
                )
                wrong_view = any(item.view_id != view_id for item in items)
                if undeclared:
                    raise ValueError(
                        f"specialist {view_id} emitted undeclared slots: {undeclared}"
                    )
                if wrong_view:
                    raise ValueError(
                        f"specialist {view_id} emitted evidence for another view"
                    )
                outcome = "ok"
                span.set_output({"outcome": outcome, "evidence_count": len(items)})
            except Exception as exc:
                error_type = type(exc).__name__
                span.set_output({"outcome": "error", "error_type": error_type})
                items = ()
            latency_ms = (time.perf_counter() - started) * 1000
            return items, SpecialistRun(
                view_id=view_id,
                outcome=outcome,
                latency_ms=latency_ms,
                evidence_count=len(items),
                error_type=error_type,
            )

    @staticmethod
    def _bundle(
        request: EvidenceSwarmRequest, evidence: Sequence[Evidence]
    ) -> SwarmEvidenceBundle:
        safe = tuple(filter_evidence(evidence))
        protected_count = len(evidence) - len(safe)
        slot_fills: dict[str, list[Any]] = {}
        provenance: list[Mapping[str, Any]] = []
        for item in safe:
            slot_fills.setdefault(item.slot, []).append(item.value)
            provenance.append(
                {
                    "source_id": item.source_id,
                    "view_id": item.view_id,
                    **dict(item.provenance),
                }
            )
        missing = tuple(
            slot for slot in request.required_slots if slot not in slot_fills
        )
        return SwarmEvidenceBundle(
            intent_id=request.intent_id,
            required_slots=request.required_slots,
            slot_fills={slot: tuple(values) for slot, values in slot_fills.items()},
            missing_slots=missing,
            selected_evidence=safe,
            protected_evidence_count=protected_count,
            conflict_verification=verify_conflicts(safe),
            provenance=tuple(provenance),
        )


def _deterministic_answer(bundle: SwarmEvidenceBundle) -> str:
    filled = ", ".join(sorted(bundle.slot_fills)) or "none"
    missing = ", ".join(bundle.missing_slots) or "none"
    status = bundle.conflict_verification.get("status", "consistent")
    return (
        f"Evidence slots: {filled}. Missing slots: {missing}. Verification: {status}."
    )


__all__ = [
    "EvidenceSpecialist",
    "EvidenceSwarmCoordinator",
    "EvidenceSwarmRequest",
    "EvidenceSwarmResult",
    "SpecialistRun",
    "SwarmEvidenceBundle",
]
