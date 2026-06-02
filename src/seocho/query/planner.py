from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Sequence

from ..store.llm import complete_with_task_hints
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
        question_hints = builder.derive_schema_hints(question)
        response = self._complete(
            system=builder.intent_extraction_prompt(schema_hints=question_hints),
            user=f'Question:\n"""\n{question}\n"""',
            temperature=0.0,
            response_format={"type": "json_object"},
            reasoning_mode=False,
            task_hint="intent_classification",
        )

        try:
            intent_data = response.json()
        except (json.JSONDecodeError, ValueError):
            logger.error("LLM returned non-JSON intent: %s", response.text[:200])
            intent_data = {"intent": "neighbors", "anchor_entity": question}

        intent_data = builder.normalize_intent(question, intent_data)
        schema_hints = builder.derive_schema_hints(
            question,
            raw_intent=intent_data,
            resolved_entities=[
                str(intent_data.get("anchor_entity", "") or "").strip(),
                str(intent_data.get("target_entity", "") or "").strip(),
            ],
            label_hints=question_hints.get("label_candidates", []),
        )
        intent_data["schema_hints"] = schema_hints

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
                schema_hints=schema_hints,
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
            "\n"
            "Task:\n"
            "- Generate one relaxed alternative query plan after earlier attempts failed.\n\n"
            "Context:\n"
            f'- Ontology: "{ctx["ontology_name"]}".\n'
            f"--- Graph Schema ---\n{ctx['graph_schema']}\n\n"
            f"Previous attempts:\n{attempts_summary}\n\n"
            "Constraints:\n"
            "- Use broader match patterns such as CONTAINS instead of exact match when needed.\n"
            "- Try alternative relationship paths supported by the schema.\n"
            "- Remove overly specific filters that likely caused zero results.\n"
            "- Fall back to listing available entities only if relationship lookup is unsupported.\n"
            "- Keep the query read-only.\n\n"
            "Output format:\n"
            '- Return exactly one valid json object: {"cypher": "...", "params": {...}, "strategy": "..."}\n\n'
            "Verification:\n"
            "- Before finalizing, check that the Cypher uses only schema-supported labels, properties, and relationships.\n"
            "- Check that every parameter referenced in the Cypher exists in params."
        )
        response = self._complete(
            system=system,
            user=f'Original question:\n"""\n{question}\n"""',
            temperature=0.2,
            response_format={"type": "json_object"},
            reasoning_mode=True,
            task_hint="query_repair",
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

    def _complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float,
        response_format: Optional[Dict[str, Any]] = None,
        reasoning_mode: Optional[bool] = None,
        task_hint: Optional[str] = None,
    ) -> Any:
        return complete_with_task_hints(
            self.llm,
            system=system,
            user=user,
            temperature=temperature,
            response_format=response_format,
            reasoning_mode=reasoning_mode,
            task_hint=task_hint,
        )
