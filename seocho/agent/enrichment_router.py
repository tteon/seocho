"""Query enrichment + parallel fan-out router (ADR-0091).

Runs as a pre-stage in ``Session.ask()`` and as the canonical
``augment_fn`` for ``GraphAgenticLoop``. One implementation, two callers.

Four stages:

1. **augment** — produce the ``augmentation`` dict that
   ``RoutingPolicy.decide()`` already expects:
   ``{intent, entities, topic, rewrite}``.
2. **route** — call ``RoutingPolicy.decide()`` with the augmentation.
3. **fan_out** — run the selected backends concurrently (asyncio.gather).
4. **fuse** — collapse ranked lists with ``ReciprocalRankFusion``.

Thin slice scope: Cypher backend only; vector / fulltext / GDS backends
return empty ranked lists. The augmentation step uses a keyword-based
intent classifier over ``seocho.query.intent.INTENT_CATALOG``; the LLM
classifier upgrade is a follow-up. Short-circuit on high-confidence single
entity match is also a follow-up.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence

from seocho.routing import RoutingDecision, RoutingPolicy

from .fusion import Fusion, ReciprocalRankFusion


def enrichment_router_enabled() -> bool:
    """ADR-0091 feature flag — opt-in until the integration milestone."""
    return os.environ.get("SEOCHO_ENABLE_ENRICHMENT_ROUTER", "").strip() in {"1", "true", "TRUE"}


_QUOTED_RE = re.compile(r'"([^"]+)"')
_CASED_RE = re.compile(r"\b([A-Z][A-Za-z0-9.&'-]{2,})\b")


@dataclass
class FanOutResult:
    """Per-backend output collected during ``fan_out``."""

    backend: str
    items: List[Any] = field(default_factory=list)
    elapsed_ms: int = 0
    error: Optional[str] = None


@dataclass
class RouterTrace:
    """Per-call audit record for the router pass."""

    workspace_id: str
    question: str
    augmentation: Dict[str, Any]
    decision: Dict[str, Any]
    backends_run: List[str]
    fan_out: Dict[str, Dict[str, Any]]
    fused_top_k_ids: List[str]
    short_circuit: Optional[str] = None


class QueryEnrichmentRouter:
    """Augment → route → fan out → fuse.

    Backend callables accept ``(question, augmentation, decision)`` and
    return a ranked list of evidence items (dicts with an ``id`` field).
    ``None`` for any backend means it is not configured and will be
    skipped regardless of policy weight.

    Args:
        policy: existing ``RoutingPolicy``; the router does not own it.
        cypher_backend: callable for the Cypher backend (required for the
            thin slice; the other three default to None).
        vector_backend / fulltext_backend / gds_backend: optional callables.
        fusion: fusion strategy; defaults to ``ReciprocalRankFusion``.
        intent_classifier: callable ``(question) -> {intent, confidence,
            reason}``; defaults to a small keyword classifier.
        entity_extractor: callable ``(question) -> List[str]``; defaults
            to a quoted/cased-token heuristic.
        topic_extractor: callable ``(question) -> List[str]``; defaults
            to an empty list (LLM upgrade is a follow-up).
        per_backend_timeout_seconds: per-call timeout for fan-out.
    """

    def __init__(
        self,
        *,
        policy: RoutingPolicy,
        cypher_backend: Optional[Callable[..., Any]] = None,
        vector_backend: Optional[Callable[..., Any]] = None,
        fulltext_backend: Optional[Callable[..., Any]] = None,
        gds_backend: Optional[Callable[..., Any]] = None,
        fusion: Optional[Fusion] = None,
        intent_classifier: Optional[Callable[[str], Dict[str, Any]]] = None,
        entity_extractor: Optional[Callable[[str], List[str]]] = None,
        topic_extractor: Optional[Callable[[str], List[str]]] = None,
        per_backend_timeout_seconds: float = 10.0,
    ) -> None:
        self.policy = policy
        self._backends: Dict[str, Optional[Callable[..., Any]]] = {
            "cypher": cypher_backend,
            "vector": vector_backend,
            "fulltext": fulltext_backend,
            "gds": gds_backend,
        }
        self.fusion: Fusion = fusion or ReciprocalRankFusion()
        self._intent_classifier = intent_classifier or _default_intent_classifier
        self._entity_extractor = entity_extractor or _default_entity_extractor
        self._topic_extractor = topic_extractor or _default_topic_extractor
        self._timeout = float(per_backend_timeout_seconds)

    # ---- Stage 1: augment ------------------------------------------------

    def augment(self, question: str, workspace_id: str = "default") -> Dict[str, Any]:
        intent = self._intent_classifier(question or "")
        entities = self._entity_extractor(question or "")
        topics = self._topic_extractor(question or "")
        return {
            "intent": intent,
            "entities": entities,
            "topic": topics,
            "rewrite": None,
            "workspace_id": workspace_id,
        }

    # ---- Stage 2: route --------------------------------------------------

    def route(self, augmentation: Mapping[str, Any], *, model: str = "gpt-4o-mini") -> RoutingDecision:
        return self.policy.decide(augmentation=augmentation, model=model)

    # ---- Stage 3: fan out ------------------------------------------------

    async def fan_out(
        self,
        question: str,
        augmentation: Mapping[str, Any],
        decision: RoutingDecision,
    ) -> Dict[str, FanOutResult]:
        weights = dict(decision.weights or {})
        floor = getattr(self.fusion, "_weight_floor", 0.10)
        selected: List[str] = [
            b for b, w in weights.items()
            if w >= floor and self._backends.get(b) is not None
        ]

        async def _run_one(backend: str) -> FanOutResult:
            fn = self._backends[backend]
            assert fn is not None
            import time as _time

            t0 = _time.perf_counter()
            try:
                maybe_coro = fn(question, augmentation, decision)
                if isinstance(maybe_coro, Awaitable):  # type: ignore[arg-type]
                    items = await asyncio.wait_for(maybe_coro, timeout=self._timeout)
                else:
                    items = maybe_coro
                if not isinstance(items, list):
                    items = list(items) if items else []
            except asyncio.TimeoutError:
                return FanOutResult(
                    backend=backend,
                    elapsed_ms=int((_time.perf_counter() - t0) * 1000),
                    error="timeout",
                )
            except Exception as exc:  # noqa: BLE001
                return FanOutResult(
                    backend=backend,
                    elapsed_ms=int((_time.perf_counter() - t0) * 1000),
                    error=str(exc),
                )
            return FanOutResult(
                backend=backend,
                items=list(items),
                elapsed_ms=int((_time.perf_counter() - t0) * 1000),
            )

        if not selected:
            return {}

        results = await asyncio.gather(*[_run_one(b) for b in selected])
        return {r.backend: r for r in results}

    # ---- Stage 4: fuse ---------------------------------------------------

    def fuse(
        self,
        fan_out_results: Mapping[str, FanOutResult],
        decision: RoutingDecision,
    ) -> List[Dict[str, Any]]:
        ranked = {b: r.items for b, r in fan_out_results.items() if not r.error}
        return self.fusion.fuse(ranked, decision.weights or {})

    # ---- One-shot ---------------------------------------------------------

    async def aroute(
        self,
        question: str,
        *,
        workspace_id: str = "default",
        model: str = "gpt-4o-mini",
    ) -> tuple[List[Dict[str, Any]], RouterTrace]:
        """Run all four stages and return ``(fused, trace)``."""
        augmentation = self.augment(question, workspace_id=workspace_id)
        decision = self.route(augmentation, model=model)
        fan_out_results = await self.fan_out(question, augmentation, decision)
        fused = self.fuse(fan_out_results, decision)
        trace = RouterTrace(
            workspace_id=workspace_id,
            question=question,
            augmentation=augmentation,
            decision={
                "intent": decision.intent,
                "weights": dict(decision.weights or {}),
                "refused": bool(decision.refused),
            },
            backends_run=sorted(fan_out_results.keys()),
            fan_out={
                b: {"count": len(r.items), "elapsed_ms": r.elapsed_ms, "error": r.error}
                for b, r in fan_out_results.items()
            },
            fused_top_k_ids=[entry["id"] for entry in fused[:10]],
        )
        return fused, trace

    def run(
        self,
        question: str,
        *,
        workspace_id: str = "default",
        model: str = "gpt-4o-mini",
    ) -> tuple[List[Dict[str, Any]], RouterTrace]:
        """Sync wrapper around ``aroute`` for callers that don't have a loop."""
        return asyncio.run(self.aroute(question, workspace_id=workspace_id, model=model))


# ---- Defaults ------------------------------------------------------------


_INTENT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("relationship_lookup", ("relation", "relationship", "related", "connected", "between", "link")),
    ("path_lookup", ("path", "chain", "trace", "via")),
    ("aggregation", ("count", "how many", "total", "sum", "average")),
    ("analytics", ("centrality", "hub", "community", "cluster", "pagerank", "similar")),
    ("entity_lookup", ("what is", "who is", "describe", "look up", "find")),
)


def _default_intent_classifier(question: str) -> Dict[str, Any]:
    q = (question or "").lower()
    for label, keywords in _INTENT_KEYWORDS:
        if any(kw in q for kw in keywords):
            return {"intent": label, "confidence": 0.75, "reason": "keyword_match"}
    return {"intent": "lookup", "confidence": 0.65, "reason": "default"}


def _default_entity_extractor(question: str) -> List[str]:
    quoted = _QUOTED_RE.findall(question or "")
    cased = _CASED_RE.findall(question or "")
    seen: Dict[str, None] = {}
    for ent in [*quoted, *cased]:
        seen[ent] = None
    return list(seen.keys())[:6]


def _default_topic_extractor(_question: str) -> List[str]:
    # Topic extraction needs the per-workspace ontology context (ADR-0073).
    # The thin slice keeps it empty; the LLM-driven upgrade lands with the
    # integration milestone in Phase 5.
    return []


__all__ = [
    "QueryEnrichmentRouter",
    "FanOutResult",
    "RouterTrace",
    "enrichment_router_enabled",
]
