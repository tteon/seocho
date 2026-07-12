import pytest

from seocho.memory import validate_projection_format


def _payload():
    common = {
        "workspace_id": "tenant-a",
        "memory_sequence": 7,
        "schema_version": "agent-memory.v1",
    }
    nodes = [
        {"id": "intent-1", "label": "TransactionIntent", "properties": common},
        {"id": "order-1", "label": "Order", "properties": common},
    ]
    relationships = [
        {
            "source": "intent-1",
            "target": "order-1",
            "type": "MATERIALIZED_AS",
            "source_label": "TransactionIntent",
            "target_label": "Order",
            "properties": common,
        }
    ]
    return nodes, relationships


def test_projection_format_accepts_typed_auditable_batch() -> None:
    validate_projection_format(*_payload())


def test_projection_format_rejects_untyped_relationship_endpoint() -> None:
    nodes, relationships = _payload()
    relationships[0].pop("target_label")
    with pytest.raises(ValueError, match="target_label"):
        validate_projection_format(nodes, relationships)


def test_projection_format_rejects_endpoint_label_drift() -> None:
    nodes, relationships = _payload()
    relationships[0]["target_label"] = "Fill"
    with pytest.raises(ValueError, match="endpoint labels"):
        validate_projection_format(nodes, relationships)
