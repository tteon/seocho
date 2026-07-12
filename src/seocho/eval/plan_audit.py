"""GOpt-inspired execution-plan audit contracts.

This module does not claim to embed GOpt. It closes the operational loop around
SEOCHO's existing GOPTS ranking/latency layers: preserve a PROFILE snapshot,
identify the operator where work or cardinality diverges, apply a schema-aware
candidate rewrite, and compare the plan and latency without changing semantics.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class PlanOperator:
    operator: str
    details: str = ""
    estimated_rows: float | None = None
    actual_rows: float | None = None
    db_hits: float | None = None

    @property
    def cardinality_ratio(self) -> float | None:
        if self.actual_rows is None or self.estimated_rows is None:
            return None
        return self.actual_rows / max(self.estimated_rows, 1e-9)


@dataclass(frozen=True, slots=True)
class PlanAudit:
    fingerprint: str
    operators: tuple[PlanOperator, ...]
    findings: tuple[str, ...]
    total_db_hits: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "operators": [asdict(operator) for operator in self.operators],
            "findings": list(self.findings),
            "total_db_hits": self.total_db_hits,
        }


@dataclass(frozen=True, slots=True)
class PlanComparison:
    baseline: PlanAudit
    candidate: PlanAudit
    baseline_p95_ms: float
    candidate_p95_ms: float
    semantic_parity: bool

    @property
    def speedup(self) -> float:
        if self.candidate_p95_ms <= 0:
            return 0.0
        return self.baseline_p95_ms / self.candidate_p95_ms

    @property
    def db_hits_reduction(self) -> float:
        if self.baseline.total_db_hits <= 0:
            return 0.0
        return 1.0 - self.candidate.total_db_hits / self.baseline.total_db_hits

    @property
    def promotable(self) -> bool:
        return self.semantic_parity and self.speedup > 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline.to_dict(),
            "candidate": self.candidate.to_dict(),
            "baseline_p95_ms": self.baseline_p95_ms,
            "candidate_p95_ms": self.candidate_p95_ms,
            "speedup": self.speedup,
            "db_hits_reduction": self.db_hits_reduction,
            "semantic_parity": self.semantic_parity,
            "promotable": self.promotable,
        }


def _number(arguments: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = arguments.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return None


def _flatten(plan: Any) -> Iterable[PlanOperator]:
    if isinstance(plan, Mapping):
        operator = str(plan.get("operatorType", plan.get("operator_type", "unknown")))
        arguments = plan.get("args", plan.get("arguments", {})) or {}
        children = plan.get("children", ()) or ()
    else:
        operator = str(plan.operator_type)
        arguments = plan.arguments or {}
        children = plan.children or ()
    yield PlanOperator(
        operator=operator,
        details=str(arguments.get("Details", ""))[:240],
        estimated_rows=_number(arguments, "EstimatedRows", "estimatedRows"),
        actual_rows=_number(arguments, "Rows", "rows"),
        db_hits=_number(arguments, "DbHits", "DB Hits", "dbHits"),
    )
    for child in children:
        yield from _flatten(child)


def audit_profile(plan: Any, *, cardinality_ratio_threshold: float = 10.0) -> PlanAudit:
    """Convert a Neo4j/DozerDB PROFILE plan into stable diagnostic evidence."""

    operators = tuple(_flatten(plan))
    names = tuple(operator.operator for operator in operators)
    findings: set[str] = set()
    if any("AllNodesScan" in name for name in names):
        findings.add("global_node_scan")
    if any("VarLengthExpand" in name for name in names):
        findings.add("variable_length_expansion")
    if any("EagerAggregation" in name for name in names):
        findings.add("eager_aggregation")
    if any("Sort" in name or "Top" in name for name in names):
        findings.add("sort_or_top")
    if any(
        ratio is not None
        and (ratio >= cardinality_ratio_threshold or 0 < ratio <= 1 / cardinality_ratio_threshold)
        for ratio in (operator.cardinality_ratio for operator in operators)
    ):
        findings.add("cardinality_misestimation")
    fingerprint_input = "|".join(
        f"{operator.operator}:{operator.details}" for operator in operators
    )
    return PlanAudit(
        fingerprint=hashlib.sha256(fingerprint_input.encode()).hexdigest()[:16],
        operators=operators,
        findings=tuple(sorted(findings)),
        total_db_hits=sum(operator.db_hits or 0.0 for operator in operators),
    )


def compare_plans(
    baseline: PlanAudit,
    candidate: PlanAudit,
    *,
    baseline_p95_ms: float,
    candidate_p95_ms: float,
    baseline_result_hashes: Sequence[str],
    candidate_result_hashes: Sequence[str],
) -> PlanComparison:
    """Require result parity before a faster candidate can be promoted."""

    return PlanComparison(
        baseline=baseline,
        candidate=candidate,
        baseline_p95_ms=baseline_p95_ms,
        candidate_p95_ms=candidate_p95_ms,
        semantic_parity=tuple(baseline_result_hashes) == tuple(candidate_result_hashes),
    )


def emit_plan_comparison_metrics(
    comparison: PlanComparison,
    *,
    cohort: str,
) -> None:
    """Emit only bounded aggregate dimensions; detailed plans belong in traces."""

    from ..metrics import get_metrics

    metrics = get_metrics()
    if comparison.semantic_parity:
        metrics.record(
            "seocho.query.plan.speedup", comparison.speedup, {"cohort": cohort}
        )
        metrics.record(
            "seocho.query.plan.db_hits_reduction",
            max(0.0, comparison.db_hits_reduction),
            {"cohort": cohort},
        )
    for variant, audit in (
        ("baseline", comparison.baseline),
        ("candidate", comparison.candidate),
    ):
        for finding in audit.findings:
            metrics.add(
                "seocho.query.plan.finding.count",
                attributes={"variant": variant, "finding": finding},
            )


__all__ = [
    "PlanAudit",
    "PlanComparison",
    "PlanOperator",
    "audit_profile",
    "compare_plans",
    "emit_plan_comparison_metrics",
]
