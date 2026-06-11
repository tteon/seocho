"""Semantic-layer query lane (ADR-0103, slice S4).

Ties the three validated pieces into one callable path:
  decompose (S6) → arbitrate (S5) → compile (S2) → execute → format.

Returns a `SemanticResult` whose `answer` is set ONLY when the arbiter routed
STRUCTURED and the exact-key Cypher returned a row; otherwise `answer` is None
and `route` carries the arbiter's hint (NARRATIVE / CLARIFY / FAIL) so the
caller can fall through to the narrative/chunk path or surface a clarification
— never a silent empty structured result dressed as an answer.

MARA-first / bge-only: the decomposer uses whatever `llm` is passed; slot
resolution uses the optional bge `scorer`. No OpenAI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from ..semantic_layer import ObservationSlots, default_registry, default_resolver
from ..semantic_layer.compile import compile_observation_lookup
from .arbiter import CLARIFY, STRUCTURED, ArbiterHint, arbitrate, make_graph_probe
from .semantic_decompose import QuerySlots, decompose


@dataclass(frozen=True, slots=True)
class SemanticResult:
    route: str
    answer: Optional[str] = None
    rows: List[dict] = field(default_factory=list)
    slots: Optional[ObservationSlots] = None
    query_slots: Optional[QuerySlots] = None
    hint: Optional[ArbiterHint] = None


def clarification_message(hint: Any) -> str:
    """User-facing clarification for a CLARIFY route (H3 operational policy).

    Turns the arbiter's gap into an actionable question instead of a silent
    empty result — e.g. offer the periods the graph actually holds.
    """
    missing = set(getattr(hint, "missing_slots", ()) or ())
    periods = getattr(hint, "available_periods", ()) or ()
    if "period" in missing or (hint and hint.route == CLARIFY and periods):
        years = sorted({p.split(":")[1] for p in periods if p.count(":") >= 2})
        if years:
            return ("Which fiscal year are you asking about? "
                    f"Available: {', '.join('FY' + y for y in years)}.")
        return "Which fiscal year are you asking about?"
    if "entity" in missing:
        return "Which company are you asking about?"
    if "concept" in missing:
        return "Which financial metric are you asking about?"
    return "Could you clarify your question?"


def format_observation(row: dict) -> str:
    """Deterministic terse answer from a returned Observation row (no LLM)."""
    value, unit = row.get("value"), str(row.get("unit") or "")
    period = str(row.get("period") or "")
    if isinstance(value, (int, float)) and unit.upper() == "USD":
        body = f"${value / 1_000_000:,.0f} million"
    else:
        body = f"{value} {unit}".strip()
    return f"{body} ({period})" if period else body


def semantic_answer(
    question: str,
    *,
    llm: Any,
    graph_store: Any,
    database: str = "neo4j",
    workspace_id: str = "",
    registry: Any = None,
    resolver: Any = None,
    scorer: Any = None,
    manifests: Any = None,
) -> SemanticResult:
    registry = registry or default_registry()
    resolver = resolver or default_resolver()
    ontology_id = "finance"

    if manifests:
        # v2: pick WHICH ontology this question belongs to, then resolve within it
        from .arbiter import select_ontology
        from .semantic_decompose import decompose_question, resolve_slots

        qs = decompose_question(question, llm=llm)
        if qs is None:
            return SemanticResult(route="FAIL",
                                  slots=ObservationSlots(unresolved=("decompose_failed",)))
        match = select_ontology(qs.metric_surface, manifests, scorer=scorer)
        if match.manifest is None:
            return SemanticResult(route="NARRATIVE", query_slots=qs,
                                  slots=ObservationSlots(unresolved=("ontology",)))
        registry, resolver = match.manifest.registry, match.manifest.resolver
        ontology_id = match.ontology_id
        slots = resolve_slots(qs, registry=registry, resolver=resolver, scorer=scorer)
    else:
        qs, slots = decompose(question, llm=llm, registry=registry,
                              resolver=resolver, scorer=scorer)

    probe = make_graph_probe(graph_store, database=database, workspace_id=workspace_id)
    hint = arbitrate(slots, probe_fn=probe, ontology_id=ontology_id)

    if hint.route != STRUCTURED:
        return SemanticResult(route=hint.route, slots=slots, query_slots=qs, hint=hint)

    cypher, params = compile_observation_lookup(slots, workspace_id=workspace_id)
    try:
        rows = graph_store.query(cypher, params=params, database=database) or []
    except Exception:
        rows = []
    if not rows:
        # arbiter said STRUCTURED but execution was empty — do not fabricate;
        # let the caller fall through (route demoted to NARRATIVE).
        return SemanticResult(route="NARRATIVE", slots=slots, query_slots=qs, hint=hint)

    return SemanticResult(route=STRUCTURED, answer=format_observation(rows[0]),
                          rows=rows, slots=slots, query_slots=qs, hint=hint)
