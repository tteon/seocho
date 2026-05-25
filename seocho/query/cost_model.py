"""GOPTS cost model — deterministic linear scoring for Cypher plans (ADR-0097).

The cost model is the input ranker for G2's K-candidate plan
enumerator. It does *not* execute Cypher; it estimates a relative cost
from PatternSpec metadata + the IndexStats payload that G1's
``Neo4jGraphStore.get_index_stats()`` produces.

Linear scoring intentionally crude — per ADR-0097:

    cost = α · plan_depth
         + β · estimated_row_count
         + γ · index_miss_penalty
         + δ · cartesian_risk

Coefficients default to ``DEFAULT_COEFFICIENTS`` here; ``RoutingPolicy.thresholds``
exposes the same keys so per-deployment overrides flow through the
policy layer (ADR-0091). Per-workspace coefficient tuning is a follow-up.

Lower cost = better. Returned scores are comparable across plans for
the *same* question; they are not calibrated across questions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .contracts import PatternSpec


# ---------------------------------------------------------------------------
# Default coefficients (mirror RoutingPolicy.thresholds defaults)
# ---------------------------------------------------------------------------


DEFAULT_COEFFICIENTS: Dict[str, float] = {
    "cost_alpha_plan_depth": 1.0,
    "cost_beta_estimated_row_count": 0.001,
    "cost_gamma_index_miss_penalty": 50.0,
    "cost_delta_cartesian_risk": 100.0,
}


# ---------------------------------------------------------------------------
# Plan-shape → estimated depth lookup
#
# Each cypher_shape carries an intrinsic depth — how many MATCH /
# expansion steps the generated plan walks. Path queries dominate
# because they can be unbounded. ``count`` and ``list_all`` are 0
# because they touch only one label without traversal.
# ---------------------------------------------------------------------------


_PLAN_DEPTH_BY_SHAPE: Dict[str, int] = {
    "entity_lookup": 1,
    "relationship_lookup": 2,
    "financial_metric_lookup": 2,
    "financial_metric_delta": 3,  # scans multiple years
    "neighbors": 2,
    "path": 5,
    "count": 0,
    "list_all": 0,
}


@dataclass
class CostBreakdown:
    """Per-component cost contribution for trace auditability (ADR-0097 §9)."""

    pattern_id: str
    cypher_shape: str
    plan_depth: int
    estimated_row_count: int
    index_miss_penalty: int
    cartesian_risk: int
    components: Dict[str, float] = field(default_factory=dict)
    total: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "cypher_shape": self.cypher_shape,
            "plan_depth": self.plan_depth,
            "estimated_row_count": self.estimated_row_count,
            "index_miss_penalty": self.index_miss_penalty,
            "cartesian_risk": self.cartesian_risk,
            "components": dict(self.components),
            "total": self.total,
        }


# ---------------------------------------------------------------------------
# Cost computation
# ---------------------------------------------------------------------------


def cost(
    pattern: PatternSpec,
    *,
    index_stats: Optional[Dict[str, Any]] = None,
    coefficients: Optional[Dict[str, float]] = None,
) -> CostBreakdown:
    """Score a PatternSpec for a given index_stats payload.

    Returns a CostBreakdown so the ranker and the trace can both see
    the per-component contributions, not just the scalar total.
    """
    coeffs = dict(DEFAULT_COEFFICIENTS, **(coefficients or {}))
    stats = index_stats or {}

    plan_depth = _PLAN_DEPTH_BY_SHAPE.get(pattern.cypher_shape, 2)

    # Estimated row count is the cardinality of the first required label
    # (or the cheapest one if multiple are listed). For patterns with no
    # required_labels, default to the median label count or 1000 if no
    # stats are available — keeps the cost finite without anchoring to 0.
    label_counts = stats.get("label_counts") or {}
    if pattern.required_labels:
        row_estimates = [
            label_counts.get(label, 0) for label in pattern.required_labels
        ]
        estimated_rows = min(row_estimates) if row_estimates else 0
    elif label_counts:
        sorted_counts = sorted(label_counts.values())
        estimated_rows = sorted_counts[len(sorted_counts) // 2]
    else:
        estimated_rows = 1000

    # Index miss = required label has no matching index in IndexStats.
    # An index "matches" when its labels_or_types covers a required label.
    indexes = stats.get("indexes") or []
    indexed_labels = set()
    for idx in indexes:
        labels = idx.get("labels_or_types") or []
        indexed_labels.update(labels)
    index_miss = sum(
        1 for label in pattern.required_labels if label not in indexed_labels
    )

    # Cartesian risk flag — patterns whose cost_hints mark them as
    # multi-MATCH or unbounded-path get the full penalty; others get 0.
    cartesian_risk = 0
    if pattern.cost_hints.get("unbounded_path"):
        cartesian_risk = 1
    if pattern.cost_hints.get("scans_multiple_years"):
        cartesian_risk = max(cartesian_risk, 1)

    components = {
        "plan_depth": coeffs["cost_alpha_plan_depth"] * plan_depth,
        "estimated_row_count": coeffs["cost_beta_estimated_row_count"] * estimated_rows,
        "index_miss_penalty": coeffs["cost_gamma_index_miss_penalty"] * index_miss,
        "cartesian_risk": coeffs["cost_delta_cartesian_risk"] * cartesian_risk,
    }
    total = sum(components.values())

    return CostBreakdown(
        pattern_id=pattern.pattern_id,
        cypher_shape=pattern.cypher_shape,
        plan_depth=plan_depth,
        estimated_row_count=estimated_rows,
        index_miss_penalty=index_miss,
        cartesian_risk=cartesian_risk,
        components=components,
        total=total,
    )


def rank_candidates(
    candidates: List[PatternSpec],
    *,
    index_stats: Optional[Dict[str, Any]] = None,
    coefficients: Optional[Dict[str, float]] = None,
) -> List[Tuple[PatternSpec, CostBreakdown]]:
    """Score every candidate and return them sorted ascending by total cost.

    Ties broken by ``pattern_id`` to keep the order deterministic across
    runs (a non-deterministic tie-break would make G4's regression
    harness flake when two plans have equal cost).
    """
    scored = [
        (pattern, cost(pattern, index_stats=index_stats, coefficients=coefficients))
        for pattern in candidates
    ]
    scored.sort(key=lambda item: (item[1].total, item[0].pattern_id))
    return scored
