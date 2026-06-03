"""NL → QuerySlots decomposition + slot resolution (ADR-0103, slice S6).

Phase-1 of the semantic-layer query path: the LLM SELECTS over a closed
vocabulary instead of generating Cypher. It emits surface forms + a structural
intent; deterministic resolution (closed concept registry, entity→CIK, period
normalization, with a bge grounding fallback for fuzzy metric surfaces) turns
those into a canonical `ObservationSlots`. A slot that doesn't resolve is
recorded in `unresolved` so the arbiter (S5) can route it to CLARIFY/FAIL
rather than letting the compiler emit a fuzzy query.

MARA-first: the LLM call uses whatever backend is passed (default benchmark
wiring is provider="mara"); resolution embeddings use bge via the optional
`scorer`. No OpenAI.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from ..semantic_layer import ObservationSlots, normalize_period
from ..semantic_layer.concepts import ConceptRegistry
from ..semantic_layer.identity import EntityResolver
from .ontology_grounding import ground

_INTENTS = ("metric_lookup", "narrative_lookup", "other")

DECOMPOSE_SYSTEM = (
    "You convert a financial question into structured slots. You do NOT write "
    "queries. Return ONLY a JSON object with these keys:\n"
    '  "intent": one of ["metric_lookup","narrative_lookup","other"]\n'
    '  "metric_surface": the metric phrase copied from the question '
    '(e.g. "total revenue", "net income"); "" if none\n'
    '  "entity_surface": the company name or ticker copied from the question; "" if none\n'
    '  "period": the fiscal period phrase (e.g. "FY2024", "fiscal year 2023"); "" if none\n'
    '  "aggregation": one of ["none","sum","avg","max","min","delta"]\n'
    "Rules: copy the user's wording for metric/entity — do NOT normalize or "
    "invent. Use metric_lookup when the question asks for a specific reported "
    "figure. Output the JSON object only, no prose."
)


@dataclass(frozen=True, slots=True)
class QuerySlots:
    intent: str
    metric_surface: str
    entity_surface: str
    period: str
    aggregation: str = "none"


def parse_slots(text: str) -> Optional[QuerySlots]:
    """Extract + validate a QuerySlots JSON object from raw LLM text."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    intent = str(obj.get("intent", "")).strip()
    if intent not in _INTENTS:
        return None
    agg = str(obj.get("aggregation", "none")).strip() or "none"
    return QuerySlots(
        intent=intent,
        metric_surface=str(obj.get("metric_surface", "")).strip(),
        entity_surface=str(obj.get("entity_surface", "")).strip(),
        period=str(obj.get("period", "")).strip(),
        aggregation=agg,
    )


def decompose_question(question: str, *, llm: Any, repair: int = 1) -> Optional[QuerySlots]:
    """Call the LLM (MARA) to produce QuerySlots; one validate-then-repair retry."""
    user = f"Question: {question}"
    last_text = ""
    for attempt in range(repair + 1):
        prompt = user if attempt == 0 else (
            f"{user}\n\nYour previous reply was not a valid QuerySlots JSON "
            f'object (got: {last_text[:160]!r}). Return ONLY the JSON object '
            f"with the required keys and allowed enum values."
        )
        resp = llm.complete(
            system=DECOMPOSE_SYSTEM, user=prompt, temperature=0.0,
            response_format={"type": "json_object"},
        )
        last_text = getattr(resp, "text", "") or ""
        slots = parse_slots(last_text)
        if slots is not None:
            return slots
    return None


def _resolve_concept(surface: str, registry: ConceptRegistry,
                     scorer: Any, threshold: float) -> Optional[str]:
    if not surface:
        return None
    exact = registry.resolve(surface)
    if exact:
        return exact
    # bge (or lexical) grounding fallback over the closed alias surfaces
    ranked = ground(surface, list(registry.candidate_surfaces),
                    top_k=1, threshold=threshold, scorer=scorer)
    return registry.resolve(ranked[0][0]) if ranked else None


def resolve_slots(
    qs: QuerySlots,
    *,
    registry: ConceptRegistry,
    resolver: EntityResolver,
    scorer: Any = None,
    threshold: float = 0.45,
    unit: str = "USD",
) -> ObservationSlots:
    """Resolve surface QuerySlots → canonical ObservationSlots (unresolved-aware)."""
    unresolved: List[str] = []

    concept_id = _resolve_concept(qs.metric_surface, registry, scorer, threshold)
    if not concept_id:
        unresolved.append("concept")

    entity_cik = resolver.resolve(qs.entity_surface) if qs.entity_surface else None
    if not entity_cik:
        unresolved.append("entity")

    period_key = normalize_period(qs.period) if qs.period else None
    if not period_key:
        unresolved.append("period")

    return ObservationSlots(
        entity_cik=entity_cik or "",
        concept_id=concept_id or "",
        period_keys=(period_key,) if period_key else (),
        unit=unit,
        basis="consolidated",
        unresolved=tuple(unresolved),
    )


def decompose(
    question: str,
    *,
    llm: Any,
    registry: ConceptRegistry,
    resolver: EntityResolver,
    scorer: Any = None,
    threshold: float = 0.45,
) -> Tuple[Optional[QuerySlots], ObservationSlots]:
    """Full Phase-1: question → (QuerySlots, resolved ObservationSlots)."""
    qs = decompose_question(question, llm=llm)
    if qs is None:
        return None, ObservationSlots(unresolved=("decompose_failed",))
    return qs, resolve_slots(qs, registry=registry, resolver=resolver,
                             scorer=scorer, threshold=threshold)
