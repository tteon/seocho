"""Declarative routing policy for the runtime query path.

Closes seocho-mcg1.

Background
----------
Routing inside the runtime today is implicit: intent inference happens, then
weights and refusal logic are inlined per call site. The Ch 4 appendix
(``examples/teaching/chapter-04-routing-decision-design.md``) consolidates
the decision tree, confidence thresholds, context-window budget, temporal
staleness penalty, and refusal contract — this module codifies the same
contract as a single object so we can:

- log a stable :class:`RoutingDecision` per request to Opik
- tune thresholds via YAML / dict without code edits
- combine confidence with :func:`staleness_penalty` so freshness is part of
  the routing score

This is intentionally small: it does *not* replace the agent runtime nor the
search backends — it returns a decision object describing *which backends to
call*, *with what weights*, and *whether to refuse*.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Optional


# ---------------------------------------------------------------------------
# Default thresholds — match the Ch 4 appendix verbatim
# ---------------------------------------------------------------------------


DEFAULT_THRESHOLDS: Dict[str, float] = {
    "intent_high": 0.80,
    "intent_fallback": 0.60,
    "entity_keep": 0.50,
    "entity_hard_use": 0.75,
    "community_narrow": 0.55,
    "staleness_soft_days": 30,
    "staleness_hard_days": 365,
    "refusal_avg_confidence": 0.30,
}


DEFAULT_WEIGHTS: Dict[str, Dict[str, float]] = {
    "lookup":      {"vector": 0.20, "fulltext": 0.30, "cypher": 1.00},
    "aggregation": {"vector": 0.00, "fulltext": 0.00, "cypher": 1.00},
    "comparison":  {"vector": 0.80, "fulltext": 0.80, "cypher": 0.80},
    "explanation": {"vector": 1.00, "fulltext": 0.40, "cypher": 0.00},
}

FALLBACK_WEIGHTS: Dict[str, float] = {"vector": 0.6, "fulltext": 0.6, "cypher": 0.6}


MODEL_CONTEXT: Dict[str, int] = {
    "gpt-4o-mini": 128_000,
    "gpt-4o": 128_000,
    "kimi-k2.5": 200_000,
    "deepseek-chat": 64_000,
    "grok-4.20-reasoning": 128_000,
}


# ---------------------------------------------------------------------------
# Decision model
# ---------------------------------------------------------------------------


@dataclass
class RoutingDecision:
    """Output of :meth:`RoutingPolicy.decide`.

    All fields are stable; emit the dict via :meth:`to_metadata` for Opik.
    """

    intent: str
    weights: Dict[str, float]
    top_n: int
    context_budget_tokens: int
    refusal_reason: Optional[str] = None
    gate_triggered: Optional[str] = None
    notes: Dict[str, Any] = field(default_factory=dict)

    @property
    def refused(self) -> bool:
        return self.refusal_reason is not None

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "routing.intent": self.intent,
            "routing.weights": self.weights,
            "routing.top_n": self.top_n,
            "routing.context_budget": self.context_budget_tokens,
            "routing.gate_triggered": self.gate_triggered,
            "routing.refusal_reason": self.refusal_reason,
            **{f"routing.note.{k}": v for k, v in self.notes.items()},
        }


# ---------------------------------------------------------------------------
# Staleness penalty
# ---------------------------------------------------------------------------


def staleness_penalty(
    published_at: Optional[datetime], *, half_life_days: float = 180
) -> float:
    """Exponential decay; 1.0 means fresh, 0.0 means infinitely stale."""
    if published_at is None:
        return 1.0
    now = datetime.now(timezone.utc) if published_at.tzinfo else datetime.utcnow()
    age_days = max(0, (now - published_at).days)
    return math.exp(-math.log(2) * age_days / max(1.0, half_life_days))


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass
class RoutingPolicy:
    """Declarative routing policy.

    ``decide()`` accepts the augmentation result produced by the 4-axis
    pipeline (intent/entity/topic/rewrite). Thresholds and backend weights
    are overridable per instance — see :meth:`default` and :meth:`from_dict`.
    """

    thresholds: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    weights: Dict[str, Dict[str, float]] = field(
        default_factory=lambda: {k: dict(v) for k, v in DEFAULT_WEIGHTS.items()}
    )
    fallback_weights: Dict[str, float] = field(
        default_factory=lambda: dict(FALLBACK_WEIGHTS)
    )
    model_context: Dict[str, int] = field(
        default_factory=lambda: dict(MODEL_CONTEXT)
    )

    # -- Construction helpers ---------------------------------------------

    @classmethod
    def default(cls) -> "RoutingPolicy":
        return cls()

    @classmethod
    def from_dict(cls, spec: Mapping[str, Any]) -> "RoutingPolicy":
        return cls(
            thresholds=dict(DEFAULT_THRESHOLDS, **(spec.get("thresholds") or {})),
            weights={
                k: dict(DEFAULT_WEIGHTS.get(k, {}), **(v or {}))
                for k, v in (spec.get("weights") or DEFAULT_WEIGHTS).items()
            },
            fallback_weights=dict(FALLBACK_WEIGHTS, **(spec.get("fallback_weights") or {})),
            model_context=dict(MODEL_CONTEXT, **(spec.get("model_context") or {})),
        )

    # -- Helpers ----------------------------------------------------------

    def context_budget(self, model: str, *, system_tokens: int, question_tokens: int, answer_reserve: int = 1500) -> int:
        cap = self.model_context.get(model, 8000)
        available = cap - system_tokens - question_tokens - answer_reserve
        return max(0, int(available * 0.90))

    def adaptive_top_n(self, budget: int, *, avg_chunk_tokens: int = 220) -> int:
        if avg_chunk_tokens <= 0:
            return 5
        return max(3, min(20, budget // avg_chunk_tokens))

    # -- Decision ---------------------------------------------------------

    def decide(
        self,
        *,
        augmentation: Mapping[str, Any],
        model: str,
        system_tokens: int = 600,
        question_tokens: int = 80,
        candidate_count: Optional[int] = None,
        avg_effective_confidence: Optional[float] = None,
    ) -> RoutingDecision:
        intent_payload = augmentation.get("intent") or {}
        intent = str(intent_payload.get("intent") or "explanation").lower()
        intent_conf = float(intent_payload.get("confidence") or 0.0)

        # -- weights / gate
        gate: Optional[str] = None
        if intent_conf < self.thresholds["intent_fallback"]:
            weights = dict(self.fallback_weights)
            gate = "intent_fallback"
        else:
            weights = dict(self.weights.get(intent, self.fallback_weights))

        # -- budget / top_n
        budget = self.context_budget(
            model, system_tokens=system_tokens, question_tokens=question_tokens
        )
        top_n = self.adaptive_top_n(budget)

        # -- refusal evaluation
        refusal: Optional[str] = None
        if candidate_count is not None and candidate_count <= 0:
            refusal = "no_candidates"
        elif (
            avg_effective_confidence is not None
            and avg_effective_confidence < self.thresholds["refusal_avg_confidence"]
        ):
            refusal = f"low_confidence_avg={avg_effective_confidence:.2f}"

        notes: Dict[str, Any] = {
            "intent_confidence": intent_conf,
            "candidate_count": candidate_count,
            "avg_effective_confidence": avg_effective_confidence,
        }

        return RoutingDecision(
            intent=intent,
            weights=weights,
            top_n=top_n,
            context_budget_tokens=budget,
            refusal_reason=refusal,
            gate_triggered=gate,
            notes=notes,
        )


__all__ = [
    "RoutingPolicy",
    "RoutingDecision",
    "staleness_penalty",
    "DEFAULT_THRESHOLDS",
    "DEFAULT_WEIGHTS",
    "FALLBACK_WEIGHTS",
    "MODEL_CONTEXT",
]
