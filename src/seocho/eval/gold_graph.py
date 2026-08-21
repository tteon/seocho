"""Deterministic gold-graph measurements for ontology-governance experiments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


def _text(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _triple(value: Mapping[str, Any]) -> tuple[str, str, str]:
    return (_text(value.get("source")), _text(value.get("relation") or value.get("type")), _text(value.get("target")))


@dataclass(frozen=True)
class GoldGraphScore:
    precision: float | None
    recall: float | None
    f1: float | None
    relation_direction_accuracy: float | None
    required_slot_recall: float | None
    provenance_recall: float | None

    def to_dict(self) -> dict[str, float | None]:
        return self.__dict__.copy()


def score_gold_graph(
    predicted_triples: Sequence[Mapping[str, Any]], gold_triples: Sequence[Mapping[str, Any]],
    *, required_slots: Sequence[str] = (), observed_slots: Sequence[str] = (),
    required_provenance: Sequence[str] = (), observed_provenance: Sequence[str] = (),
) -> GoldGraphScore:
    """Score semantic extraction without treating SHACL conformance as truth."""
    predicted, gold = {_triple(v) for v in predicted_triples}, {_triple(v) for v in gold_triples}
    hits = len(predicted & gold)
    precision = hits / len(predicted) if predicted else (1.0 if not gold else 0.0)
    recall = hits / len(gold) if gold else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    # A reversed edge has the same endpoints/relation but must not count as a
    # direction success. The denominator is gold relations with a predicted
    # counterpart in either direction.
    directed = [(s, r, t) for s, r, t in gold if (s, r, t) in predicted or (t, r, s) in predicted]
    direction = (sum((s, r, t) in predicted for s, r, t in directed) / len(directed)) if directed else None
    slots, seen_slots = {_text(v) for v in required_slots}, {_text(v) for v in observed_slots}
    prov, seen_prov = {_text(v) for v in required_provenance}, {_text(v) for v in observed_provenance}
    return GoldGraphScore(
        round(precision, 6), round(recall, 6), round(f1, 6),
        round(direction, 6) if direction is not None else None,
        round(len(slots & seen_slots) / len(slots), 6) if slots else None,
        round(len(prov & seen_prov) / len(prov), 6) if prov else None,
    )
