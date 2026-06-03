"""Ontology arbiter — neutral measure→hint routing (ADR-0103, slice S5).

The arbiter is the only component that sees BOTH whether the question resolved
to closed-vocab slots (which the compiler can't) AND whether the graph actually
holds the corresponding observation (which the decomposer can't). It MEASURES
those and emits a routing HINT; it does NOT decide — the planner consults the
hint just before choosing a path. Read-only, no synthesis LLM, no reasoning
(honors the "reasoning off the hot path" guardrail): at most one cheap bounded
graph probe.

This is what turns a silent empty structured result into an explicit,
observable route — the mechanism that would have caught the real-MD&A 0.00 case
(concept resolves but no Observation exists for that (cik,concept,period) →
NARRATIVE/CLARIFY instead of an empty answer dressed as correct).

v1: single finance ontology; `ontology_id` is reserved for v2 multi-ontology
selection (same interface, non-breaking).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

from ..semantic_layer import ObservationSlots

# Route hints (the planner is the decider; these are signals).
STRUCTURED = "STRUCTURED"   # slots resolve + graph holds the observation → exact-key compile
NARRATIVE = "NARRATIVE"     # qualitative / OOV / graph lacks the fact → chunk/narrative path
CLARIFY = "CLARIFY"         # resolvable but a slot (period) is missing/absent → ask
FAIL = "FAIL"               # nothing usable (decomposition failed)


@dataclass(frozen=True, slots=True)
class GraphProbe:
    """Cheap, bounded read result: does the graph hold this (cik, concept)?"""
    entity_has_concept: bool
    available_periods: Tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ArbiterHint:
    route: str
    ontology_id: str = "finance"
    concept_id: str = ""
    entity_key: str = ""
    period_keys: Tuple[str, ...] = ()
    missing_slots: Tuple[str, ...] = ()
    graph_has_data: bool = False
    available_periods: Tuple[str, ...] = ()
    confidence: Dict[str, Any] = field(default_factory=dict)
    rationale: str = ""

    def to_span(self) -> Dict[str, Any]:
        """Flat attributes for a tracing span (observability of the route)."""
        return {
            "arbiter.route": self.route,
            "arbiter.ontology_id": self.ontology_id,
            "arbiter.concept_id": self.concept_id,
            "arbiter.entity_key": self.entity_key,
            "arbiter.graph_has_data": self.graph_has_data,
            "arbiter.missing_slots": ",".join(self.missing_slots),
            "arbiter.rationale": self.rationale,
        }


def make_graph_probe(graph_store: Any, database: str = "neo4j",
                     workspace_id: str = "") -> Callable[[ObservationSlots], GraphProbe]:
    """Build a probe_fn that reads DISTINCT period_keys for (cik, concept).

    One bounded, read-only query — no traversal beyond the entity's
    observations of the requested concept. Returns an empty probe on any error
    (the arbiter then treats it as "graph lacks data").
    """
    def probe(slots: ObservationSlots) -> GraphProbe:
        if not (slots.entity_cik and slots.concept_id):
            return GraphProbe(entity_has_concept=False)
        try:
            rows = graph_store.query(
                "MATCH (c:Company {cik: $cik})-[:HAS_OBSERVATION]->"
                "(o:Observation {concept_id: $concept_id}) "
                "WHERE ($ws = '' OR o.workspace_id = $ws) "
                "RETURN collect(DISTINCT o.period_key) AS periods",
                params={"cik": slots.entity_cik, "concept_id": slots.concept_id,
                        "ws": workspace_id},
                database=database,
            )
        except Exception:
            return GraphProbe(entity_has_concept=False)
        periods = tuple(rows[0]["periods"]) if rows and rows[0].get("periods") else ()
        return GraphProbe(entity_has_concept=bool(periods), available_periods=periods)


    return probe


def arbitrate(
    slots: ObservationSlots,
    *,
    probe_fn: Optional[Callable[[ObservationSlots], GraphProbe]] = None,
    ontology_id: str = "finance",
) -> ArbiterHint:
    """Measure slot-resolvability + graph contents → a routing hint (no decision).

    Deterministic decision table (auditable, hot-path-cheap):
      - decomposition failed                       → FAIL
      - concept/entity unresolved (OOV)            → NARRATIVE (chunk/narrative may hold it)
      - period unresolved                          → CLARIFY (ask for the period)
      - all resolved, graph lacks (cik,concept)    → NARRATIVE (qualitative attempt)
      - all resolved, requested period absent      → CLARIFY (offer available periods)
      - all resolved, period present in graph      → STRUCTURED (exact-key compile will hit)
    """
    unresolved = set(slots.unresolved)

    if "decompose_failed" in unresolved:
        return ArbiterHint(route=FAIL, ontology_id=ontology_id,
                           missing_slots=tuple(slots.unresolved),
                           rationale="decomposition failed")

    base = dict(ontology_id=ontology_id, concept_id=slots.concept_id,
                entity_key=slots.entity_cik, period_keys=slots.period_keys,
                missing_slots=tuple(slots.unresolved))

    if "concept" in unresolved or "entity" in unresolved:
        return ArbiterHint(route=NARRATIVE, **base,
                           confidence={"slots_resolved": False},
                           rationale="metric/entity out of closed vocabulary; "
                                     "try narrative/chunk path")
    if "period" in unresolved:
        return ArbiterHint(route=CLARIFY, **base,
                           confidence={"slots_resolved": False},
                           rationale="period not specified; ask the user")

    # all slots resolved — measure the graph
    probe = probe_fn(slots) if probe_fn else GraphProbe(entity_has_concept=False)
    if not probe.entity_has_concept:
        return ArbiterHint(route=NARRATIVE, graph_has_data=False, **base,
                           confidence={"slots_resolved": True, "graph_has_data": False},
                           rationale="graph holds no observation for this "
                                     "(entity, concept); try narrative/chunk path")

    requested = set(slots.period_keys)
    present = requested & set(probe.available_periods)
    if not present:
        return ArbiterHint(route=CLARIFY, graph_has_data=True,
                           available_periods=probe.available_periods, **base,
                           confidence={"slots_resolved": True, "graph_has_data": True},
                           rationale="requested period not in graph; offer available periods")

    return ArbiterHint(route=STRUCTURED, graph_has_data=True,
                       available_periods=probe.available_periods, **base,
                       confidence={"slots_resolved": True, "graph_has_data": True},
                       rationale="slots resolved and observation present; exact-key compile")
