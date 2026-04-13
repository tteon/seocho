from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Sequence

from .contracts import QueryPlan
from .cypher_builder import CypherBuilder

logger = logging.getLogger(__name__)


class DeterministicQueryPlanner:
    """Canonical local query planner for ontology-aware Cypher generation."""

    def __init__(self, *, ontology: Any, llm: Any, workspace_id: str) -> None:
        self.ontology = ontology
        self.llm = llm
        self.workspace_id = workspace_id

    def plan(self, question: str) -> QueryPlan:
        builder = CypherBuilder(self.ontology)
        response = self.llm.complete(
            system=builder.intent_extraction_prompt(),
            user=f"Question: {question}",
            temperature=0.0,
            response_format={"type": "json_object"},
        )

        try:
            intent_data = response.json()
        except (json.JSONDecodeError, ValueError):
            logger.error("LLM returned non-JSON intent: %s", response.text[:200])
            intent_data = {"intent": "neighbors", "anchor_entity": question}

        intent_data = builder.normalize_intent(question, intent_data)

        try:
            cypher, params = builder.build(
                intent=intent_data.get("intent", "neighbors"),
                anchor_entity=intent_data.get("anchor_entity", question),
                anchor_label=intent_data.get("anchor_label", ""),
                target_entity=intent_data.get("target_entity", ""),
                target_label=intent_data.get("target_label", ""),
                relationship_type=intent_data.get("relationship_type", ""),
                metric_name=intent_data.get("metric_name", ""),
                metric_aliases=intent_data.get("metric_aliases", ()),
                metric_scope_tokens=intent_data.get("metric_scope_tokens", ()),
                years=intent_data.get("years", ()),
                workspace_id=self.workspace_id,
            )
        except Exception as exc:
            logger.error("Cypher build failed: %s", exc)
            return QueryPlan(
                question=question,
                cypher="",
                params={},
                intent_data=intent_data,
                error="I could not build a query for your question.",
            )

        if not cypher:
            return QueryPlan(
                question=question,
                cypher="",
                params={},
                intent_data=intent_data,
                error="I could not determine how to query the graph.",
            )
        return QueryPlan(question=question, cypher=cypher, params=params, intent_data=intent_data)

    def repair(
        self,
        *,
        question: str,
        attempts: Sequence[Dict[str, Any]],
        intent_data: Optional[Dict[str, Any]] = None,
        ontology: Optional[Any] = None,
    ) -> QueryPlan:
        active_ontology = ontology or self.ontology
        if intent_data and str(intent_data.get("intent", "")).startswith("financial_metric_"):
            return QueryPlan(
                question=question,
                cypher="",
                params={},
                intent_data=dict(intent_data),
                error="Deterministic finance query returned no supported evidence.",
            )

        ctx = active_ontology.to_query_context()
        attempts_summary = "\n".join(
            f"  Attempt {i+1}: {a['cypher'][:100]}... → {a['result_count']} results"
            + (f" (error: {a['error']})" if a.get("error") else "")
            for i, a in enumerate(attempts)
        )

        system = (
            "You are a knowledge graph query repair agent.\n"
            f"Working with ontology \"{ctx['ontology_name']}\".\n\n"
            f"--- Graph Schema ---\n{ctx['graph_schema']}\n\n"
            f"The previous queries returned no results:\n{attempts_summary}\n\n"
            "Generate a RELAXED alternative query that:\n"
            "- Uses broader match patterns (CONTAINS instead of exact match)\n"
            "- Tries alternative relationship paths\n"
            "- Removes overly specific filters\n"
            "- Falls back to listing available entities if all else fails\n\n"
            'Return JSON: {"cypher": "...", "params": {...}, "strategy": "..."}'
        )
        response = self.llm.complete(
            system=system,
            user=f"Original question: {question}",
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            return QueryPlan(
                question=question,
                cypher="",
                params={},
                intent_data=dict(intent_data or {}),
                error="Repair query generation failed",
            )
        return QueryPlan(
            question=question,
            cypher=str(payload.get("cypher", "") or ""),
            params=dict(payload.get("params", {}) or {}),
            intent_data=dict(intent_data or {}),
            error=None,
        )

