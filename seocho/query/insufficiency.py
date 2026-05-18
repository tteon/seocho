from __future__ import annotations

from typing import Any, Dict, Sequence, Set

from .contracts import InsufficiencyAssessment
from .intent import extract_tradeoff_points_from_triples


class QueryInsufficiencyClassifier:
    """Classify whether executed graph retrieval filled the requested slots."""

    def assess(self, intent: Dict[str, Any], rows: Sequence[Dict[str, Any]]) -> InsufficiencyAssessment:
        focus_slots = [str(slot).strip() for slot in intent.get("focus_slots", []) if str(slot).strip()]
        row_count = len(rows)
        if row_count == 0:
            return InsufficiencyAssessment(
                sufficient=False,
                reason="empty_result",
                missing_slots=tuple(focus_slots),
                row_count=0,
            )

        filled_slots: Set[str] = set()
        intent_id = str(intent.get("intent_id", "")).strip()
        for row in rows:
            if row.get("source_entity"):
                filled_slots.add("source_entity")
            if row.get("target_entity"):
                filled_slots.add("target_entity")
            if row.get("relation_type") or row.get("relation_paths"):
                filled_slots.add("relation_paths")
            if row.get("owner_or_operator"):
                filled_slots.add("owner_or_operator")
            if row.get("supporting_fact") or row.get("properties") or row.get("neighbors"):
                filled_slots.add("supporting_fact")
            if row.get("limitation_points"):
                filled_slots.add("limitation_points")
            if row.get("alternative_points"):
                filled_slots.add("alternative_points")

        tradeoff_points = extract_tradeoff_points_from_triples(
            selected_triples=_triples_from_rows(rows),
        )
        if tradeoff_points["limitation_points"]:
            filled_slots.add("limitation_points")
        if tradeoff_points["alternative_points"]:
            filled_slots.add("alternative_points")

        if intent_id == "relationship_lookup" and not any(row.get("relation_type") for row in rows):
            return InsufficiencyAssessment(
                sufficient=False,
                reason="missing_relation_path",
                missing_slots=tuple(slot for slot in focus_slots if slot not in filled_slots or slot == "relation_paths"),
                row_count=row_count,
                filled_slots=tuple(sorted(filled_slots)),
            )
        if intent_id == "responsibility_lookup" and not any(row.get("owner_or_operator") for row in rows):
            return InsufficiencyAssessment(
                sufficient=False,
                reason="missing_owner_or_operator",
                missing_slots=tuple(slot for slot in focus_slots if slot not in filled_slots or slot == "owner_or_operator"),
                row_count=row_count,
                filled_slots=tuple(sorted(filled_slots)),
            )

        missing_slots = tuple(slot for slot in focus_slots if slot not in filled_slots)
        if missing_slots:
            return InsufficiencyAssessment(
                sufficient=False,
                reason="partial_slot_fill",
                missing_slots=missing_slots,
                row_count=row_count,
                filled_slots=tuple(sorted(filled_slots)),
            )
        return InsufficiencyAssessment(
            sufficient=True,
            reason="sufficient",
            missing_slots=(),
            row_count=row_count,
            filled_slots=tuple(sorted(filled_slots)),
        )


def _triples_from_rows(rows: Sequence[Dict[str, Any]]) -> list[Dict[str, Any]]:
    triples: list[Dict[str, Any]] = []
    for row in rows:
        relation_type = str(row.get("relation_type", "")).strip()
        target_entity = str(row.get("target_entity", "")).strip()
        if relation_type and target_entity:
            triples.append(
                {
                    "source": row.get("source_entity") or row.get("owner_or_operator") or "",
                    "relation": relation_type,
                    "target": target_entity,
                    "target_labels": row.get("target_labels", []),
                }
            )
        for neighbor in row.get("neighbors", []) if isinstance(row.get("neighbors"), list) else []:
            if not isinstance(neighbor, dict):
                continue
            relation = str(neighbor.get("relation", "")).strip()
            target = str(neighbor.get("target", "")).strip()
            if not relation or not target:
                continue
            triples.append(
                {
                    "source": row.get("target_entity") or row.get("source_entity") or "",
                    "relation": relation,
                    "target": target,
                    "target_labels": neighbor.get("target_labels", []),
                }
            )
    return triples
