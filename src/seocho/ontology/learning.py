"""Offline, review-only ontology-learning primitives.

The module turns *observed* extracted graph payloads into candidate terms,
taxonomy edges, non-taxonomic relation signatures, and structural axioms. It
does not mutate an :class:`Ontology`, activate a bundle, or project a graph.
Those actions remain behind the existing human review and RDF governance gates.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

from ..axioms import AxiomCandidate, mine_axioms
from ..metrics import get_metrics
from .core import Ontology


SCHEMA_VERSION = "seocho.ontology_learning_report.v1"


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class TermCandidate:
    term: str
    observed_types: tuple[str, ...]
    support: int
    evidence_refs: tuple[str, ...]

    @property
    def candidate_id(self) -> str:
        return f"term:{_digest([self.term, self.observed_types])[:16]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "candidate_id": self.candidate_id,
            "disposition": "review_required",
        }


@dataclass(frozen=True)
class EdgeCandidate:
    kind: str  # taxonomy | relation
    source_type: str
    predicate: str
    target_type: str
    support: int
    declared: bool

    @property
    def candidate_id(self) -> str:
        return f"{self.kind}:{_digest([self.source_type, self.predicate, self.target_type])[:16]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "candidate_id": self.candidate_id,
            "disposition": "review_required",
        }


@dataclass(frozen=True)
class LearningTaskScore:
    task: str
    status: str  # scored | unavailable
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    predicted: int = 0
    gold: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OntologyLearningReport:
    source_digest: str
    terms: list[TermCandidate] = field(default_factory=list)
    taxonomy: list[EdgeCandidate] = field(default_factory=list)
    relations: list[EdgeCandidate] = field(default_factory=list)
    axioms: list[AxiomCandidate] = field(default_factory=list)
    scores: list[LearningTaskScore] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source_digest": self.source_digest,
            "promotion": {
                "status": "not_attempted",
                "reason": "learning reports are review-only",
            },
            "terms": [item.to_dict() for item in self.terms],
            "taxonomy": [item.to_dict() for item in self.taxonomy],
            "relations": [item.to_dict() for item in self.relations],
            "axioms": [asdict(item) for item in self.axioms],
            "scores": [item.to_dict() for item in self.scores],
        }


def _node_types(graph: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for node in graph.get("nodes", []) or []:
        if not isinstance(node, Mapping):
            continue
        raw = node.get("labels", node.get("label", []))
        labels = (raw,) if isinstance(raw, str) else tuple(raw or ())
        labels = tuple(
            sorted(str(label).strip() for label in labels if str(label).strip())
        )
        node_id = str(node.get("id", "")).strip()
        if node_id and labels:
            result[node_id] = labels
    return result


def learn_from_graph(
    graph: Mapping[str, Any], ontology: Ontology, *, min_support: int = 2
) -> OntologyLearningReport:
    """Create a deterministic review artifact from one observed graph payload."""
    if min_support < 1:
        raise ValueError("min_support must be positive")
    started = time.perf_counter()
    node_types = _node_types(graph)
    terms: dict[tuple[str, tuple[str, ...]], list[str]] = defaultdict(list)
    for index, node in enumerate(graph.get("nodes", []) or []):
        if not isinstance(node, Mapping):
            continue
        props = node.get("properties", {}) or {}
        surface = str(props.get("name") or node.get("name") or "").strip()
        node_id = str(node.get("id", index))
        if surface and node_id in node_types:
            terms[(surface, node_types[node_id])].append(
                str(props.get("source_id") or node_id)
            )
    term_candidates = [
        TermCandidate(
            term=term,
            observed_types=types,
            support=len(refs),
            evidence_refs=tuple(sorted(set(refs))[:5]),
        )
        for (term, types), refs in terms.items()
        if len(refs) >= min_support
    ]
    term_candidates.sort(
        key=lambda item: (-item.support, item.term, item.observed_types)
    )

    tax = []
    for child, definition in ontology.nodes.items():
        for parent in getattr(definition, "broader", []) or []:
            tax.append(EdgeCandidate("taxonomy", child, "is_a", str(parent), 0, True))

    relation_support: Counter[tuple[str, str, str]] = Counter()
    for relation in graph.get("relationships", []) or []:
        if not isinstance(relation, Mapping):
            continue
        rel_type = str(relation.get("type", "")).strip()
        source = node_types.get(str(relation.get("source", "")), ())
        target = node_types.get(str(relation.get("target", "")), ())
        for source_type in source:
            for target_type in target:
                if rel_type:
                    relation_support[(source_type, rel_type, target_type)] += 1
    relations = [
        EdgeCandidate(
            "relation",
            source,
            predicate,
            target,
            support,
            predicate in ontology.relationships,
        )
        for (source, predicate, target), support in relation_support.items()
        if support >= min_support
    ]
    relations.sort(
        key=lambda item: (
            -item.support,
            item.source_type,
            item.predicate,
            item.target_type,
        )
    )
    report = OntologyLearningReport(
        source_digest=_digest(graph),
        terms=term_candidates,
        taxonomy=tax,
        relations=relations,
        axioms=mine_axioms(dict(graph), min_support=min_support),
    )
    metrics = get_metrics()
    for kind, count in (
        ("term", len(report.terms)),
        ("taxonomy", len(report.taxonomy)),
        ("relation", len(report.relations)),
        ("axiom", len(report.axioms)),
    ):
        metrics.add("seocho.ontology.learning.candidate.count", count, {"kind": kind})
    metrics.record(
        "seocho.ontology.learning.duration",
        time.perf_counter() - started,
        {"outcome": "ok"},
    )
    return report


def score_learning_task(
    task: str,
    predicted: Iterable[tuple[str, ...]],
    gold: Iterable[tuple[str, ...]] | None,
) -> LearningTaskScore:
    """Score one LLMs4OL task; unavailable gold stays unavailable, never zero."""
    actual = set(gold or ())
    found = set(predicted)
    if gold is None:
        return LearningTaskScore(task=task, status="unavailable", predicted=len(found))
    hits = len(found & actual)
    precision = hits / len(found) if found else 0.0
    recall = hits / len(actual) if actual else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return LearningTaskScore(
        task,
        "scored",
        round(precision, 4),
        round(recall, 4),
        round(f1, 4),
        len(found),
        len(actual),
    )
