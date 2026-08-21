from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional, Sequence

from ..store.llm import complete_with_task_hints
from .contracts import QueryPlan
from .cypher_builder import CypherBuilder

logger = logging.getLogger(__name__)

# A leading source-framing clause names a document, not the entity the question
# is about -- e.g. "In the narrative of 'An Unsentimental Journey through
# Cornwall', which plant ...". Left in, the intent extractor anchors on the
# quoted TITLE, the anchor matches no node, and retrieval returns 0 rows
# (measured live: ADR-0213 records=0 diagnosis). Strip only a leading clause
# that (a) starts with a framing preposition, (b) cites a quoted title, and
# (c) ends at the first comma -- conservative, so ordinary questions are
# untouched and only the framing noise is removed.
_QUOTE = "\"'‘’“”"
_FRAMING_CLAUSE = re.compile(
    r"^\s*(?:in|within|according to|based on|from|per|throughout)\b"
    r"[^,]*?[" + _QUOTE + r"][^" + _QUOTE + r"]+[" + _QUOTE + r"]"
    r"[^,]*,\s+",
    re.IGNORECASE,
)


def _strip_framing_clause(question: str) -> str:
    """Drop a leading source-framing clause so the anchor is the real subject.

    Returns the question unchanged unless a conservative framing clause matches
    AND enough question remains after it (a bare title with nothing following is
    left intact rather than stripped to empty).
    """
    q = question or ""
    m = _FRAMING_CLAUSE.match(q)
    if not m:
        return question
    remainder = q[m.end():].strip()
    if len(remainder) < 12:  # nothing substantive left -> do not strip
        return question
    return remainder


class DeterministicQueryPlanner:
    """Canonical local query planner for ontology-aware Cypher generation."""

    def __init__(self, *, ontology: Any, llm: Any, workspace_id: str) -> None:
        self.ontology = ontology
        self.llm = llm
        self.workspace_id = workspace_id

    def plan(self, question: str) -> QueryPlan:
        builder = CypherBuilder(self.ontology)
        # De-frame the question for all anchor-relevant steps; keep the original
        # on the returned QueryPlan for provenance.
        focus = _strip_framing_clause(question)
        question_hints = builder.derive_schema_hints(focus)
        response = self._complete(
            system=builder.intent_extraction_prompt(schema_hints=question_hints),
            user=f'Question:\n"""\n{focus}\n"""',
            temperature=0.0,
            response_format={"type": "json_object"},
            reasoning_mode=False,
            task_hint="intent_classification",
        )

        try:
            intent_data = response.json()
        except (json.JSONDecodeError, ValueError):
            logger.error("LLM returned non-JSON intent: %s", response.text[:200])
            intent_data = {"intent": "neighbors", "anchor_entity": focus}

        intent_data = builder.normalize_intent(focus, intent_data)
        schema_hints = builder.derive_schema_hints(
            focus,
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
                anchor_entity=intent_data.get("anchor_entity", focus),
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
        # A plan hint travels the same channel as an error, because to the
        # repair agent they are the same kind of fact: a reason the previous
        # attempt is not the answer. Without it a query that returns rows while
        # planning a full scan reads as a success and is never revisited, which
        # is exactly the query whose cost explodes as the graph grows.
        attempts_summary = "\n".join(
            f"  Attempt {i+1}: {a['cypher'][:100]}... → {a['result_count']} results"
            + (f" (error: {a['error']})" if a.get("error") else "")
            + (f"\n{a['plan_hint']}" if a.get("plan_hint") else "")
            for i, a in enumerate(attempts)
        )

        system = (
            "You are a knowledge graph query repair agent.\n"
            "\n"
            "Task:\n"
            + (
                "- The previous attempt returned rows but plans a scan. Generate one "
                "NARROWER plan that anchors on an indexed property. Do not relax the "
                "match — relaxing it makes the scan wider.\n\n"
                if any(a.get("plan_hint") for a in attempts)
                # Two different repairs share one prompt, and they pull in
                # opposite directions: an empty result wants a looser match, an
                # unsargable plan wants a tighter one. Telling the model to
                # "relax" when the problem is a full scan makes the plan worse.
                else "- Generate one relaxed alternative query plan after earlier "
                     "attempts failed.\n\n"
            )
            +
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
