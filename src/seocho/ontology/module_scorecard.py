"""Structural module scorecards and bounded agent-admission policy.

The lightweight SEOCHO ontology model records types and relationship contracts,
not OWL axiom closures. Consequently ``source_subset_valid`` and
``interface_complete`` are structural checks. SHACL and the RDF receipt remain
the semantic admission evidence.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from math import exp
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class ModuleScorecard:
    """Comparable structural measures for one explicitly declared module."""

    schema_version: str
    module_id: str
    entities: int
    classes: int
    relationships: int
    data_properties: int
    relative_size: float
    size_target_fit: float
    intra_module_distance: float | None
    relative_intra_module_distance: float | None
    cohesion: float
    inter_module_distance: float | None
    coupling: float
    redundancy: float
    encapsulation: float
    independence: bool
    attribute_richness: float | None
    inheritance_richness: float | None
    source_subset_valid: bool
    interface_complete: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModuleQualityPolicy:
    """Explicit, versioned policy; it is not a claim of ontology truth."""

    schema_version: str = "seocho.module_quality_policy.v1"
    target_entities: int = 250
    min_size_target_fit: float = 0.20
    min_cohesion: float = 0.01
    max_coupling: float = 0.35
    max_redundancy: float = 0.20
    min_encapsulation: float = 0.70
    max_inter_module_distance: float = 2.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModuleQualityDecision:
    """Safe agent action derived from a scorecard, without requesting CoT."""

    disposition: str  # ready | needs_reasoning | reject
    additional_verification_calls: int
    reasons: tuple[str, ...]
    scorecard: ModuleScorecard
    policy: ModuleQualityPolicy

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["reasons"] = list(self.reasons)
        return result


def _mean_shortest_distance(nodes: set[str], edges: Sequence[tuple[str, str]]) -> float | None:
    if len(nodes) < 2:
        return 0.0
    adjacency: dict[str, set[str]] = {node: set() for node in nodes}
    for source, target in edges:
        if source in adjacency and target in adjacency:
            adjacency[source].add(target)
            adjacency[target].add(source)
    distances: list[int] = []
    for source in nodes:
        queue: deque[tuple[str, int]] = deque([(source, 0)])
        seen = {source}
        while queue:
            node, distance = queue.popleft()
            if node != source:
                distances.append(distance)
            for target in adjacency[node] - seen:
                seen.add(target)
                queue.append((target, distance + 1))
    return round(sum(distances) / len(distances), 6) if distances else None


def score_module(
    ontology: Any,
    *,
    module_id: str,
    class_names: Iterable[str],
    source_ontology: Any | None = None,
    sibling_modules: Sequence[Iterable[str]] = (),
    required_relations: Iterable[str] = (),
    target_entities: int = 250,
) -> ModuleScorecard:
    """Score an explicit type boundary using available schema structure.

    ``required_relations`` declares the expected interface. This is not called
    OWL logical completeness: the in-memory model cannot prove entailment
    preservation without a reasoner.
    """
    requested = {str(item) for item in class_names}
    all_classes = {str(item) for item in ontology.nodes}
    selected = requested & all_classes
    relationships = list(ontology.relationships.items())
    internal = [
        (name, definition) for name, definition in relationships
        if definition.source in selected and definition.target in selected
    ]
    crossing = [
        (name, definition) for name, definition in relationships
        if (definition.source in selected) != (definition.target in selected)
    ]
    internal_names = {name for name, _ in internal}
    interface_names = internal_names | {name for name, _ in crossing}
    properties = sum(
        len(getattr(ontology.nodes[name], "properties", {}) or {}) for name in selected
    )
    entities = len(selected) + len(internal) + properties
    source = source_ontology or ontology
    source_classes = {str(item) for item in source.nodes}
    source_relationships = set(source.relationships)
    source_properties = sum(
        len(getattr(node, "properties", {}) or {}) for node in source.nodes.values()
    )
    source_entities = len(source_classes) + len(source_relationships) + source_properties
    module_distance = _mean_shortest_distance(
        selected, [(definition.source, definition.target) for _, definition in internal]
    )
    source_distance = _mean_shortest_distance(
        source_classes,
        [(definition.source, definition.target) for _, definition in source.relationships.items()],
    )
    possible_relations = len(selected) * (len(selected) - 1)
    cohesion = len(internal) / possible_relations if possible_relations else 1.0
    siblings = [{str(item) for item in sibling} for sibling in sibling_modules]
    duplicate_entities = sum(len(selected & sibling) for sibling in siblings)
    redundancy = duplicate_entities / max(len(selected) + duplicate_entities, 1)
    crossing_count = len(crossing)
    total_axioms = len(internal) + crossing_count + properties
    encapsulation = 1.0 - crossing_count / total_axioms if total_axioms else 1.0
    broader_links = sum(
        1 for definition in ontology.nodes.values()
        for parent in (getattr(definition, "broader", []) or [])
        if parent in selected
    )
    required = {str(item) for item in required_relations}
    target = max(int(target_entities), 1)
    inter_distance = 1.0 if crossing_count else (0.0 if not siblings else None)
    return ModuleScorecard(
        schema_version="seocho.ontology_module_scorecard.v1",
        module_id=module_id,
        entities=entities,
        classes=len(selected),
        relationships=len(internal),
        data_properties=properties,
        relative_size=round(entities / max(source_entities, 1), 6),
        # A monotonic target-fit is safer than treating a historic periodic
        # size formula as a universal property of every ontology workload.
        size_target_fit=round(exp(-abs(entities - target) / target), 6),
        intra_module_distance=module_distance,
        relative_intra_module_distance=(
            round(source_distance / module_distance, 6)
            if source_distance is not None and module_distance not in (None, 0.0)
            else None
        ),
        cohesion=round(cohesion, 6),
        inter_module_distance=inter_distance,
        coupling=round(crossing_count / max(entities, 1), 6),
        redundancy=round(redundancy, 6),
        encapsulation=round(encapsulation, 6),
        independence=crossing_count == 0 and encapsulation == 1.0,
        attribute_richness=round(properties / len(selected), 6) if selected else None,
        inheritance_richness=round(broader_links / len(selected), 6) if selected else None,
        source_subset_valid=requested <= source_classes and internal_names <= source_relationships,
        interface_complete=required <= interface_names,
    )


def decide_module_quality(
    scorecard: ModuleScorecard, *, policy: ModuleQualityPolicy | None = None
) -> ModuleQualityDecision:
    """Turn scorecard values into a bounded retrieval/verification action."""
    active = policy or ModuleQualityPolicy()
    hard: list[str] = []
    soft: list[str] = []
    if not scorecard.source_subset_valid:
        hard.append("declared module is not a valid subset of its source ontology")
    if not scorecard.interface_complete:
        hard.append("declared required relation interface is incomplete")
    if scorecard.size_target_fit < active.min_size_target_fit:
        soft.append("module size is outside the configured target range")
    if scorecard.cohesion < active.min_cohesion:
        soft.append("module cohesion is below policy")
    if scorecard.coupling > active.max_coupling:
        soft.append("module coupling exceeds policy")
    if scorecard.redundancy > active.max_redundancy:
        soft.append("module redundancy exceeds policy")
    if scorecard.encapsulation < active.min_encapsulation:
        soft.append("module encapsulation is below policy")
    if (scorecard.inter_module_distance is not None
            and scorecard.inter_module_distance > active.max_inter_module_distance):
        soft.append("inter-module distance exceeds policy")
    if hard:
        return ModuleQualityDecision("reject", 0, tuple(hard + soft), scorecard, active)
    if soft:
        return ModuleQualityDecision(
            "needs_reasoning", min(3, len(soft)), tuple(soft), scorecard, active
        )
    return ModuleQualityDecision("ready", 0, (), scorecard, active)


def quality_gate_payload(decision: ModuleQualityDecision) -> Mapping[str, Any]:
    """Small model-safe control payload; the full scorecard stays traceable."""
    action = {
        "ready": "use the bounded profile normally",
        "needs_reasoning": "retrieve a narrow ontology slice and verify its interface before graph work",
        "reject": "do not use this profile for graph work; ask the host to repair or select another ontology version",
    }[decision.disposition]
    return {
        "disposition": decision.disposition,
        "additional_verification_calls": decision.additional_verification_calls,
        "required_action": action,
        "reasons": list(decision.reasons),
    }
