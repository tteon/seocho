"""Typed PostgreSQL-outbox to property-graph projection format."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


REQUIRED_PROJECTION_PROPERTIES = frozenset(
    {"workspace_id", "memory_sequence", "schema_version"}
)


def validate_projection_format(
    nodes: Sequence[Mapping[str, Any]],
    relationships: Sequence[Mapping[str, Any]],
) -> None:
    """Fail before graph I/O when a projection cannot use typed index seeks.

    Every relationship carries its full ``source-label / type / target-label``
    triplet. Both nodes and relationships retain the authoritative workspace,
    sequence, and schema version needed for replay and audit.
    """

    labels_by_id: dict[str, str] = {}
    for node in nodes:
        node_id = str(node.get("id", "")).strip()
        label = str(node.get("label", "")).strip()
        properties = node.get("properties", {})
        if not node_id or not label:
            raise ValueError("projection node requires id and label")
        if not isinstance(properties, Mapping):
            raise ValueError("projection node properties must be a mapping")
        missing = REQUIRED_PROJECTION_PROPERTIES - set(properties)
        if missing:
            raise ValueError(
                "projection node missing properties: " + ", ".join(sorted(missing))
            )
        labels_by_id[node_id] = label
    for relationship in relationships:
        source = str(relationship.get("source", "")).strip()
        target = str(relationship.get("target", "")).strip()
        rel_type = str(relationship.get("type", "")).strip()
        source_label = str(relationship.get("source_label", "")).strip()
        target_label = str(relationship.get("target_label", "")).strip()
        properties = relationship.get("properties", {})
        if not source or not target or not rel_type or not source_label or not target_label:
            raise ValueError(
                "projection relationship requires source, target, type, source_label, and target_label"
            )
        if labels_by_id.get(source) != source_label or labels_by_id.get(target) != target_label:
            raise ValueError("projection relationship endpoint labels do not match nodes")
        if not isinstance(properties, Mapping):
            raise ValueError("projection relationship properties must be a mapping")
        missing = REQUIRED_PROJECTION_PROPERTIES - set(properties)
        if missing:
            raise ValueError(
                "projection relationship missing properties: "
                + ", ".join(sorted(missing))
            )


__all__ = ["REQUIRED_PROJECTION_PROPERTIES", "validate_projection_format"]
