"""Cost-aware model routing — send each request to the cheapest model good enough.

SEOCHO already routes execution *modes* (pipeline/agent/supervisor) and graph
*backends* (LPG/RDF), but every request used a single static LLM. This adds the
missing axis — *model* selection.

A router has two halves (kept separate, per the standard design): an **entry
point** that speaks one request format to many providers (SEOCHO already has
this in :mod:`seocho.store.llm` — ``create_llm_backend`` + provider specs), and
a **decision** of which model to use. This module is the decision half.

It uses the **"route on a known signal"** strategy rather than predicting
difficulty from raw text: the runtime already knows the query intent
(lookup/aggregation/comparison/explanation), the query_mode
(semantic/graph_cot), and the agent task, so each maps to a tier via a cheap,
deterministic, debuggable lookup. Predicting difficulty from text is the
fallback you only need when no such signal exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = ["ModelTier", "RouteResult", "ModelRouter", "estimate_workload_cost"]


class ModelTier(IntEnum):
    """Ordered cost/capability tiers. Higher = more capable and more expensive."""

    FAST = 0       # cheap: routine work (lookups, summaries, commit messages)
    BALANCED = 1   # mid: moderate reasoning (aggregation, semantic QA, extraction)
    FRONTIER = 2   # expensive: hard reasoning (comparison, explanation, graph-CoT, planning, debate)


# Known-signal → tier maps. These are the lookups the article calls "route on a
# signal you already have": cheap to run, predictable, easy to debug.
_INTENT_TIER: Dict[str, ModelTier] = {
    "lookup": ModelTier.FAST,
    "aggregation": ModelTier.BALANCED,
    "comparison": ModelTier.FRONTIER,
    "explanation": ModelTier.FRONTIER,
}
_QUERY_MODE_TIER: Dict[str, ModelTier] = {
    "semantic": ModelTier.BALANCED,
    "graph_cot": ModelTier.FRONTIER,
}
_TASK_TIER: Dict[str, ModelTier] = {
    # background / cheap chores
    "commit_message": ModelTier.FAST,
    "summarize": ModelTier.FAST,
    "rename": ModelTier.FAST,
    "title": ModelTier.FAST,
    "format": ModelTier.FAST,
    # structured-but-routine
    "extract": ModelTier.BALANCED,
    "link": ModelTier.BALANCED,
    "validate": ModelTier.BALANCED,
    # genuinely hard
    "plan": ModelTier.FRONTIER,
    "debate": ModelTier.FRONTIER,
    "synthesize": ModelTier.FRONTIER,
}

# Illustrative *relative* cost per tier (frontier ~10x fast — the gap Kilo
# measured between their top and balanced tiers). Used only for the cost
# estimate/regression; real per-token prices belong in deployment config.
_RELATIVE_COST: Dict[ModelTier, float] = {
    ModelTier.FAST: 1.0,
    ModelTier.BALANCED: 3.0,
    ModelTier.FRONTIER: 10.0,
}


@dataclass(frozen=True)
class RouteResult:
    model: str
    tier: ModelTier
    signal: str   # which signal we routed on (e.g. "intent=lookup")
    reason: str


@dataclass
class ModelRouter:
    """Map a known signal → tier → concrete model.

    ``tier_models`` maps each :class:`ModelTier` to a model id (provider-agnostic;
    the entry-point backend resolves the provider). ``default_tier`` is used when
    no signal resolves. If a requested tier has no model, the nearest available
    tier is used (prefer same, then closest) so a partial map never crashes.
    """

    tier_models: Dict[ModelTier, str]
    default_tier: ModelTier = ModelTier.BALANCED

    def __post_init__(self) -> None:
        if not self.tier_models:
            raise ValueError("tier_models must map at least one ModelTier to a model id")

    def _resolve_model(self, tier: ModelTier) -> Tuple[str, ModelTier]:
        # nearest-available tier: same first, then closest by distance, ties cheaper.
        order = sorted(self.tier_models, key=lambda t: (abs(int(t) - int(tier)), int(t)))
        chosen = order[0]
        return self.tier_models[chosen], chosen

    def tier_for(
        self,
        *,
        intent: Optional[str] = None,
        query_mode: Optional[str] = None,
        task: Optional[str] = None,
    ) -> Tuple[ModelTier, str]:
        """Pick a tier from the most specific known signal available.

        Priority: explicit task > query_mode > intent. The most specific signal
        wins because a task label ("commit_message") is a stronger statement of
        difficulty than a coarse intent.
        """
        if task and task.lower() in _TASK_TIER:
            return _TASK_TIER[task.lower()], f"task={task.lower()}"
        if query_mode and query_mode.lower() in _QUERY_MODE_TIER:
            return _QUERY_MODE_TIER[query_mode.lower()], f"query_mode={query_mode.lower()}"
        if intent and intent.lower() in _INTENT_TIER:
            return _INTENT_TIER[intent.lower()], f"intent={intent.lower()}"
        return self.default_tier, "default"

    def route(
        self,
        *,
        intent: Optional[str] = None,
        query_mode: Optional[str] = None,
        task: Optional[str] = None,
        escalate: int = 0,
    ) -> RouteResult:
        """Resolve a model for a request.

        ``escalate`` bumps the tier up by N steps (capped at FRONTIER). This is
        the hook for ReAct/repair loops: when a cheap model fails a step, the
        next attempt escalates rather than retrying the same model.
        """
        tier, signal = self.tier_for(intent=intent, query_mode=query_mode, task=task)
        if escalate:
            tier = ModelTier(min(int(ModelTier.FRONTIER), int(tier) + max(0, escalate)))
            signal = f"{signal}+escalate{escalate}"
        model, resolved = self._resolve_model(tier)
        return RouteResult(
            model=model, tier=resolved, signal=signal,
            reason=f"routed on {signal} → {resolved.name} ({model})",
        )

    def relative_cost(self, tier: ModelTier) -> float:
        return _RELATIVE_COST[tier]

    @classmethod
    def mara_default(cls) -> "ModelRouter":
        """Default tiers backed by MARA-hosted models (all verified reachable).

        FAST=DeepSeek-V3.1, BALANCED=MiniMax-M2.5, FRONTIER=MiniMax-M2.7. Swap via
        deployment config; the router is provider-agnostic.
        """
        return cls(
            tier_models={
                ModelTier.FAST: "DeepSeek-V3.1",
                ModelTier.BALANCED: "MiniMax-M2.5",
                ModelTier.FRONTIER: "MiniMax-M2.7",
            },
            default_tier=ModelTier.BALANCED,
        )


def estimate_workload_cost(
    router: "ModelRouter",
    signals: Sequence[Dict[str, Optional[str]]],
) -> Dict[str, float]:
    """Estimate routed vs all-frontier cost over a workload of request signals.

    Each item in ``signals`` is a kwargs dict for :meth:`ModelRouter.route`
    (e.g. ``{"intent": "lookup"}``). Returns relative totals + the saving ratio
    so a regression test can assert the benefit on a realistic request mix.
    """
    routed = 0.0
    frontier = 0.0
    tiers: List[ModelTier] = []
    for sig in signals:
        result = router.route(**sig)  # type: ignore[arg-type]
        routed += router.relative_cost(result.tier)
        frontier += router.relative_cost(ModelTier.FRONTIER)
        tiers.append(result.tier)
    saving = (frontier - routed) / frontier if frontier else 0.0
    return {
        "routed_cost": routed,
        "all_frontier_cost": frontier,
        "saving_ratio": saving,
        "n": float(len(signals)),
    }
