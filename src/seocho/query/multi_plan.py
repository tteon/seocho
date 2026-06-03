"""Multi-plan execution + result fusion (ADR-0100, F8).

Gives the RouteProfile MULTI_STEP planner real teeth: for multi-hop
questions, build the top-K candidate Cypher shapes, execute each
read-only, and fuse the record sets with Reciprocal Rank Fusion (reused
from seocho/agent/fusion.py, ADR-0091). A single query shape often
under-retrieves on compositional / multi-hop questions; running several
shapes and fusing lifts recall.

Strictly opt-in (SEOCHO_MULTI_PLAN) and route-scoped to multi_hop in
local_engine — every other path keeps the proven single top-1 plan.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..agent.fusion import ReciprocalRankFusion
from .contracts import QueryExecution, QueryPlan

# Default candidate shapes for a multi-hop question — the three that can
# surface cross-entity evidence. Ordered; cost-ranking may reorder, but
# RRF makes the order non-critical.
DEFAULT_MULTI_HOP_SHAPES: Tuple[str, ...] = (
    "relationship_lookup",
    "neighbors",
    "entity_lookup",
)


def multi_plan_enabled() -> bool:
    """F8 multi-plan execution — DEFAULT OFF (opt-in).

    Unlike AnswerShape (proven, default-on), multi-plan is a recall/latency
    trade still under validation, so it ships opt-in via SEOCHO_MULTI_PLAN.
    """
    return str(os.environ.get("SEOCHO_MULTI_PLAN", "")).strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class PlanContribution:
    shape: str
    cypher: str
    row_count: int
    error: str = ""


@dataclass(frozen=True)
class MultiPlanResult:
    records: List[Dict[str, Any]]
    plan_provenance: Tuple[PlanContribution, ...] = ()
    fused_from: int = 0  # how many plans contributed non-empty records

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "multi_plan": True,
            "fused_from": self.fused_from,
            "record_count": len(self.records),
            "plans": [
                {"shape": c.shape, "rows": c.row_count, "error": bool(c.error)}
                for c in self.plan_provenance
            ],
        }


def execute_multi_plan(
    *,
    builder: Any,
    executor: Any,
    question: str,
    intent_data: Dict[str, Any],
    shapes: Sequence[str] = DEFAULT_MULTI_HOP_SHAPES,
    workspace_id: str = "",
    limit: int = 20,
    rrf_k: int = 60,
    max_plans: int = 4,
) -> MultiPlanResult:
    """Build, execute, and RRF-fuse up to ``max_plans`` candidate shapes.

    Each shape is built via ``CypherBuilder.build(intent=shape, ...)`` from
    the shared ``intent_data`` and executed read-only. Empty/erroring plans
    are dropped (best-effort). When 0 or 1 plans yield records the fused
    output is exactly those records (no-op fusion), so multi-plan never
    *loses* the single-plan result.
    """
    ranked_lists: Dict[str, Sequence[Any]] = {}
    provenance: List[PlanContribution] = []

    kwargs = _build_kwargs(intent_data, workspace_id=workspace_id, limit=limit)
    for shape in list(shapes)[: max(1, int(max_plans))]:
        try:
            cypher, params = builder.build(intent=shape, **kwargs)
        except Exception as exc:  # noqa: BLE001
            provenance.append(PlanContribution(shape=shape, cypher="", row_count=0, error=str(exc)[:120]))
            continue
        execution: QueryExecution = executor.execute(
            QueryPlan(question=question, cypher=cypher, params=params)
        )
        rows = list(execution.records or [])
        provenance.append(
            PlanContribution(
                shape=shape,
                cypher=cypher,
                row_count=len(rows),
                error=execution.error or "",
            )
        )
        if rows:
            ranked_lists[shape] = rows

    if not ranked_lists:
        return MultiPlanResult(records=[], plan_provenance=tuple(provenance), fused_from=0)

    if len(ranked_lists) == 1:
        only = next(iter(ranked_lists.values()))
        return MultiPlanResult(
            records=list(only), plan_provenance=tuple(provenance), fused_from=1
        )

    fused = ReciprocalRankFusion(k=rrf_k).fuse(
        ranked_lists, weights={s: 1.0 for s in ranked_lists}
    )
    fused_records = [entry["item"] for entry in fused]
    return MultiPlanResult(
        records=fused_records,
        plan_provenance=tuple(provenance),
        fused_from=len(ranked_lists),
    )


def _build_kwargs(intent_data: Dict[str, Any], *, workspace_id: str, limit: int) -> Dict[str, Any]:
    """Project intent_data onto CypherBuilder.build kwargs (minus ``intent``,
    which the caller varies per shape)."""
    d = intent_data or {}
    return {
        "anchor_entity": str(d.get("anchor_entity") or ""),
        "anchor_label": str(d.get("anchor_label") or ""),
        "target_entity": str(d.get("target_entity") or ""),
        "target_label": str(d.get("target_label") or ""),
        "relationship_type": str(d.get("relationship_type") or ""),
        "metric_name": str(d.get("metric_name") or ""),
        "metric_aliases": d.get("metric_aliases") or (),
        "metric_scope_tokens": d.get("metric_scope_tokens") or (),
        "years": d.get("years") or (),
        "workspace_id": workspace_id,
        "limit": limit,
        "schema_hints": d.get("schema_hints") or {},
    }
