"""GraphAgenticLoop — route → execute → evaluate → improve.

Closes seocho-j965 (MVP).

This wraps the existing :class:`seocho.client.Seocho` runtime with a
single-question feedback loop that records *why* each iteration ran the
way it did. The intent is to give learners a *visible* multi-agent
behaviour without yet refactoring the whole agent runtime:

1. **Augment** — derive intent (lookup / aggregation / comparison /
   explanation) + extracted entities from the question.
2. **Route** — :class:`seocho.routing.RoutingPolicy.decide` picks
   per-backend weights, refusal gate, top-N and context budget.
3. **Execute** — call ``client.ask(question)``; the existing local
   engine handles ontology-aware Cypher + answer synthesis.
4. **Evaluate** — empty result? citations valid? confidence band?
5. **Improve** — relax ontology slice, switch strategy, escalate to
   multi-LLM debate, or refuse.
6. **Stop** — :func:`seocho.debate.should_stop` decides on convergence,
   stagnation, hard caps, time/cost budgets.

Every iteration is captured as a :class:`LoopIteration` for downstream
inspection (Opik trace, learner debugging).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..routing import ModelRouter, RoutingDecision, RoutingPolicy
from ..debate import should_stop as _debate_should_stop, extract_citations
from ..gds import MetricSpec, gds_session


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class LoopIteration:
    """One pass through the route → execute → evaluate cycle."""

    iteration: int
    augmentation: Dict[str, Any]
    decision: RoutingDecision
    strategy: str  # "cypher" | "vector" | "analytics" | "debate"
    answer: str
    evaluation: Dict[str, Any]
    improvement_action: Optional[str] = None
    elapsed_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "iteration": self.iteration,
            "augmentation": self.augmentation,
            "decision": self.decision.to_metadata(),
            "strategy": self.strategy,
            "answer_preview": (self.answer or "")[:240],
            "evaluation": self.evaluation,
            "improvement_action": self.improvement_action,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class LoopResult:
    """Final outcome of a :meth:`GraphAgenticLoop.run` call."""

    question: str
    final_answer: str
    iterations: List[LoopIteration] = field(default_factory=list)
    stop_reason: str = ""
    total_ms: int = 0
    refused: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "final_answer": self.final_answer,
            "stop_reason": self.stop_reason,
            "refused": self.refused,
            "iterations": [it.to_dict() for it in self.iterations],
            "total_ms": self.total_ms,
        }

    def summary(self) -> str:
        bullets = [
            f"iter {it.iteration}: {it.strategy} → eval={it.evaluation} "
            f"action={it.improvement_action or '-'}"
            for it in self.iterations
        ]
        head = f"GraphAgenticLoop · {len(self.iterations)} iter · stop={self.stop_reason!r}"
        return head + "\n  " + "\n  ".join(bullets) if bullets else head


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_INTENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("analytics",   ("hub", "centrality", "community", "cluster", "similar",
                     "허브", "중심성", "커뮤니티", "유사")),
    ("aggregation", ("count", "average", "mean", "total", "sum", "how many",
                     "몇 개", "평균", "총", "합계")),
    ("comparison",  ("compare", "vs", "between", "공통", "차이", "다른", "비교")),
    ("explanation", ("why", "explain", "describe", "왜", "설명", "어떻게")),
)


# Map an analytics-flavoured question to a concrete GDS metric.
_ANALYTICS_KEYWORDS: tuple[tuple[MetricSpec, tuple[str, ...]], ...] = (
    (MetricSpec.NODE_SIMILARITY, ("similar", "duplicate", "유사", "중복")),
    (MetricSpec.CLUSTERING,      ("cluster", "cohesion", "응집", "클러스터")),
    (MetricSpec.LINK_PREDICTION, ("missing link", "predict", "예측", "누락")),
    (MetricSpec.DEGREE,          ("hub", "central", "허브", "중심")),  # default
)


def _pick_metric(question: str) -> MetricSpec:
    q = (question or "").lower()
    for spec, kws in _ANALYTICS_KEYWORDS:
        if any(kw in q for kw in kws):
            return spec
    return MetricSpec.DEGREE


def _heuristic_intent(question: str) -> Dict[str, Any]:
    """Light keyword classifier — works without an LLM call so the
    augmentation step stays cheap.

    Override the loop's ``augment_fn`` to plug a smarter classifier
    (e.g. ``seocho.query.intent.infer_question_intent``).
    """
    q = (question or "").lower()
    for label, keywords in _INTENT_RULES:
        if any(kw in q for kw in keywords):
            return {"intent": label, "confidence": 0.75, "reason": "keyword_match"}
    return {"intent": "lookup", "confidence": 0.65, "reason": "default"}


def _extract_quoted_entities(question: str) -> List[str]:
    """Pull tokens that look like named entities (quoted or capitalised)."""
    quoted = re.findall(r'"([^"]+)"', question)
    cased = re.findall(r"\b([A-Z][A-Za-z0-9.&'-]{2,})\b", question)
    seen: Dict[str, None] = {}
    for ent in [*quoted, *cased]:
        seen[ent] = None
    return list(seen.keys())[:6]


def _default_augment(question: str) -> Dict[str, Any]:
    intent = _heuristic_intent(question)
    return {
        "intent": intent,
        "entities": _extract_quoted_entities(question),
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class GraphAgenticLoop:
    """Single-question feedback loop sitting on top of a Seocho client.

    Usage::

        from seocho import Seocho
        from seocho.agent.graph_loop import GraphAgenticLoop

        client = Seocho(ontology=..., graph_store=..., llm=...)
        loop = GraphAgenticLoop(client)
        result = loop.run("Which companies share cybersecurity risk?")
        print(result.summary())
        for it in result.iterations:
            print(it.to_dict())
    """

    def __init__(
        self,
        client: Any,
        *,
        policy: Optional[RoutingPolicy] = None,
        model_router: Optional[ModelRouter] = None,
        max_iterations: int = 3,
        augment_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
        emit_trace_fn: Optional[Callable[[Dict[str, Any]], None]] = None,
        enable_analytics: bool = True,
        analytics_graph_name: str = "graph-loop",
        analytics_database: Optional[str] = None,
        analytics_projection: Optional[Dict[str, str]] = None,
    ) -> None:
        self.client = client
        self.policy = policy or RoutingPolicy.default()
        # Optional cost-aware model routing: when set, each ask() is sent to the
        # tier the routed intent maps to, escalating on repair iterations. When
        # None (default) no model is forced and behaviour is unchanged.
        self.model_router = model_router
        self.max_iterations = int(max_iterations)
        self.augment_fn = augment_fn or _default_augment
        self.emit_trace_fn = emit_trace_fn
        self.enable_analytics = enable_analytics
        self.analytics_graph_name = analytics_graph_name
        self.analytics_database = analytics_database
        # Default projection — Entity nodes co-occurring via MENTIONS. Override
        # for domains that store relationships under different labels.
        self.analytics_projection = analytics_projection or {
            "node_query": (
                "MATCH (e) WHERE NOT e:Source AND NOT e:Chunk "
                "RETURN id(e) AS id"
            ),
            "rel_query": (
                "MATCH (e1)<-[:MENTIONS]-(c:Chunk)-[:MENTIONS]->(e2) "
                "WHERE id(e1) < id(e2) "
                "RETURN id(e1) AS source, id(e2) AS target"
            ),
        }

    # -- Public API --------------------------------------------------------

    def run(self, question: str, *, model: str = "gpt-4o-mini") -> LoopResult:
        """Run the loop until convergence / refusal / max iterations."""
        t0 = time.perf_counter()
        result = LoopResult(question=question, final_answer="")

        # Mutable bag of strategy state — each iteration may flip these
        state: Dict[str, Any] = {
            "reasoning_mode": False,
            "repair_budget": 0,
            "answer_history": [],
        }
        # How many repair iterations have happened — drives model escalation.
        escalation = 0

        for i in range(1, self.max_iterations + 1):
            iter_t0 = time.perf_counter()

            augmentation = self.augment_fn(question)
            decision = self.policy.decide(
                augmentation=augmentation,
                model=model,
            )

            if decision.refused:
                iteration = LoopIteration(
                    iteration=i,
                    augmentation=augmentation,
                    decision=decision,
                    strategy="refused",
                    answer="",
                    evaluation={"refused": True, "reason": decision.refusal_reason},
                    elapsed_ms=int((time.perf_counter() - iter_t0) * 1000),
                )
                result.iterations.append(iteration)
                result.refused = True
                result.stop_reason = f"refusal: {decision.refusal_reason}"
                self._maybe_emit(iteration)
                break

            strategy = self._select_strategy(decision)
            answer = self._execute(
                strategy, question, state=state, decision=decision, escalation=escalation
            )
            evaluation = self._evaluate(answer, augmentation=augmentation)

            improvement = self._improvement_action(evaluation, prior=state["answer_history"])
            if improvement:
                self._apply_improvement(state, action=improvement)
                escalation += 1

            iteration = LoopIteration(
                iteration=i,
                augmentation=augmentation,
                decision=decision,
                strategy=strategy,
                answer=answer,
                evaluation=evaluation,
                improvement_action=improvement,
                elapsed_ms=int((time.perf_counter() - iter_t0) * 1000),
            )
            result.iterations.append(iteration)
            state["answer_history"].append(answer)
            self._maybe_emit(iteration)

            stop, reason = self._should_stop(result, evaluation)
            if stop:
                result.stop_reason = reason
                break

        if not result.refused:
            result.final_answer = state["answer_history"][-1] if state["answer_history"] else ""
            if not result.stop_reason:
                result.stop_reason = "max_iterations"

        result.total_ms = int((time.perf_counter() - t0) * 1000)
        return result

    # -- Internals ---------------------------------------------------------

    def _select_strategy(self, decision: RoutingDecision) -> str:
        # Analytics keyword wins over weights — the curriculum's Ch 2 (GDS)
        # surfaces should take precedence when the question clearly asks for
        # a graph metric.
        if self.enable_analytics and decision.intent == "analytics":
            return "analytics"
        w = decision.weights or {}
        ranked = sorted(w.items(), key=lambda kv: kv[1], reverse=True)
        if not ranked:
            return "cypher"
        top, _ = ranked[0]
        return {"cypher": "cypher", "vector": "vector", "fulltext": "fulltext"}.get(top, top)

    def _model_for(self, decision: RoutingDecision, escalation: int) -> Optional[str]:
        """Resolve the model for this attempt via the cost-aware router.

        Returns None when no router is configured (caller adds no model kwarg,
        preserving current behaviour). Escalation bumps the tier on repairs.
        """
        if self.model_router is None:
            return None
        return self.model_router.route(intent=decision.intent, escalate=escalation).model

    def _execute(
        self,
        strategy: str,
        question: str,
        *,
        state: Dict[str, Any],
        decision: Optional[RoutingDecision] = None,
        escalation: int = 0,
    ) -> str:
        """Strategy-aware execution.

        ``analytics`` opens a :func:`seocho.gds.gds_session` against the
        client's graph store, projects an Entity-co-occurrence graph, and
        runs the metric inferred from question keywords. Everything else
        delegates to :meth:`Seocho.ask` (Cypher + answer synthesis).
        """
        if strategy == "analytics":
            return self._execute_analytics(question)

        ask = getattr(self.client, "ask", None)
        if not callable(ask):
            raise RuntimeError(
                "GraphAgenticLoop requires a client with .ask(question) — "
                f"got {type(self.client).__name__}"
            )
        kwargs: Dict[str, Any] = {}
        if state.get("reasoning_mode"):
            kwargs["reasoning_mode"] = True
            kwargs["repair_budget"] = int(state.get("repair_budget", 0))
        if decision is not None:
            routed_model = self._model_for(decision, escalation)
            if routed_model:
                kwargs["model"] = routed_model
        try:
            return ask(question, **kwargs)
        except TypeError:
            return ask(question)

    def _execute_analytics(self, question: str) -> str:
        """Call seocho.gds with the inferred metric and render results.

        GDS procedures only run on Neo4j / DozerDB. On embedded stores the
        loop returns a friendly fallback message so the trace stays clean.
        """
        graph_store = getattr(self.client, "graph_store", None)
        if graph_store is None:
            return "(analytics unavailable — client.graph_store is None)"

        store_name = type(graph_store).__name__
        if "Neo4j" not in store_name and "Dozer" not in store_name:
            return (
                f"(analytics unsupported on {store_name} — GDS requires a Neo4j/DozerDB "
                "backend. Use Seocho(graph_store=Neo4jGraphStore(...)) for analytics.)"
            )

        database = self.analytics_database or getattr(self.client, "default_database", None)
        metric = _pick_metric(question)
        try:
            with gds_session(
                graph_store,
                name=self.analytics_graph_name,
                database=database,
            ) as g:
                g.project_cypher(
                    node_query=self.analytics_projection["node_query"],
                    rel_query=self.analytics_projection["rel_query"],
                )
                rows = g.metric(metric, top_k=10)
        except Exception as exc:
            return f"(analytics error: {type(exc).__name__}: {exc})"

        if not rows:
            return f"(no analytics rows for metric={metric.value})"
        head = f"Top-{len(rows)} by {metric.value}:"
        body = "\n".join(
            "- " + ", ".join(f"{k}={v!r}" for k, v in row.items()) for row in rows
        )
        return f"{head}\n{body}"

    def _evaluate(self, answer: str, *, augmentation: Dict[str, Any]) -> Dict[str, Any]:
        text = (answer or "").strip()
        empty = not text or text in {"근거 없음", "no_results"}
        citations = extract_citations(text)
        confidence = self._estimate_confidence(text, citations)
        return {
            "empty": empty,
            "length": len(text),
            "citations": len(citations),
            "confidence": confidence,
        }

    @staticmethod
    def _estimate_confidence(text: str, citations: Sequence) -> str:
        if not text:
            return "low"
        if citations:
            return "high"
        if len(text) < 60:
            return "low"
        return "medium"

    @staticmethod
    def _improvement_action(
        evaluation: Dict[str, Any],
        *,
        prior: Sequence[str],
    ) -> Optional[str]:
        if evaluation.get("empty"):
            return "enable_reasoning_mode"
        if evaluation.get("confidence") == "low" and len(prior) == 0:
            return "increase_repair_budget"
        if evaluation.get("citations", 0) == 0 and evaluation.get("confidence") != "high":
            return "request_citations"
        return None

    @staticmethod
    def _apply_improvement(state: Dict[str, Any], *, action: str) -> None:
        if action == "enable_reasoning_mode":
            state["reasoning_mode"] = True
            state["repair_budget"] = max(state.get("repair_budget", 0), 2)
        elif action == "increase_repair_budget":
            state["repair_budget"] = state.get("repair_budget", 0) + 1
            state["reasoning_mode"] = True
        # request_citations is informational — the next ask() call could be
        # configured with a citation-strict prompt by an extending subclass.

    def _should_stop(self, result: LoopResult, evaluation: Dict[str, Any]) -> tuple[bool, str]:
        if evaluation.get("confidence") == "high" and not evaluation.get("empty"):
            return True, "high_confidence"
        # Citation-Jaccard curve over answer-pairs (re-use debate.should_stop
        # heuristic on a simplified 1-D series).
        history = [it.answer for it in result.iterations]
        if len(history) >= 2 and history[-1] and history[-1] == history[-2]:
            return True, "no_change"
        curve = [1.0 if it.evaluation.get("confidence") == "high" else 0.0 for it in result.iterations]
        stop, reason = _debate_should_stop(
            curve,
            elapsed_ms=result.total_ms,
            tokens=0,
            max_rounds=self.max_iterations,
            convergence_threshold=0.99,
        )
        return stop, reason

    def _maybe_emit(self, iteration: LoopIteration) -> None:
        if self.emit_trace_fn is None:
            return
        try:
            self.emit_trace_fn(iteration.to_dict())
        except Exception:
            pass


__all__ = [
    "GraphAgenticLoop",
    "LoopIteration",
    "LoopResult",
]
