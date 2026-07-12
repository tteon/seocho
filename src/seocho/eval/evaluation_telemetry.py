"""Bounded metrics and traces for persisted evaluation artifacts."""

from __future__ import annotations

from typing import Any, Mapping

from seocho.metrics import ProductionMetrics, get_metrics
from seocho.tracing import start_span


def emit_scenario_status(
    scenario_id: str,
    *,
    status: str,
    attributes: Mapping[str, Any] | None = None,
    metrics: ProductionMetrics | None = None,
) -> None:
    sink = metrics or get_metrics()
    sink.set(
        "seocho.evaluation.scenario.status",
        1,
        {"scenario_id": scenario_id, "status": status},
    )
    with start_span(
        "evaluation.scenario",
        metadata={
            "seocho.scenario.id": scenario_id,
            "seocho.evaluation.status": status,
            **dict(attributes or {}),
        },
    ):
        pass


def emit_query_evaluation(
    *,
    cohort: str,
    total: int,
    correct: int,
    metrics: ProductionMetrics | None = None,
) -> None:
    sink = metrics or get_metrics()
    incorrect = total - correct
    sink.add("seocho.evaluation.query.count", correct, {"cohort": cohort, "outcome": "correct"})
    sink.add("seocho.evaluation.query.count", incorrect, {"cohort": cohort, "outcome": "incorrect"})
    sink.set("seocho.evaluation.query.accuracy", correct / total if total else 0, {"cohort": cohort})


__all__ = ["emit_query_evaluation", "emit_scenario_status"]
