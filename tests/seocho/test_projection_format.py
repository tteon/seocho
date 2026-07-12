import importlib.util
from pathlib import Path

import pytest

PYARROW_AVAILABLE = importlib.util.find_spec("pyarrow") is not None

from seocho.memory import validate_projection_format
from seocho.projection_format import (
    PROJECTION_SCHEMA_VERSION,
    read_parquet_artifact,
    rows_to_table,
    table_from_arrow_file,
    table_from_ipc,
    table_to_arrow_file,
    table_to_ipc,
    write_parquet_artifact,
)


def _graph_payload():
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
    validate_projection_format(*_graph_payload())


def test_projection_format_rejects_untyped_relationship_endpoint() -> None:
    nodes, relationships = _graph_payload()
    relationships[0].pop("target_label")
    with pytest.raises(ValueError, match="target_label"):
        validate_projection_format(nodes, relationships)


def test_projection_format_rejects_endpoint_label_drift() -> None:
    nodes, relationships = _graph_payload()
    relationships[0]["target_label"] = "Fill"
    with pytest.raises(ValueError, match="endpoint labels"):
        validate_projection_format(nodes, relationships)


def _rows():
    return [
        {
            "workspace_id": "tenant-a",
            "sequence": 2,
            "memory_id": "intent-2",
            "event_type": "exchange.fill",
            "occurred_at": "2026-07-12T00:00:02Z",
            "provenance_id": "source:2",
            "idempotency_key": "event-2",
            "schema_version": "memory.v1",
            "payload": {"amount": "0.25", "asset": "BTC"},
        },
        {
            "workspace_id": "tenant-a",
            "sequence": 1,
            "memory_id": "intent-1",
            "event_type": "exchange.intent",
            "payload": {"asset": "BTC"},
        },
    ]


@pytest.mark.skipif(not PYARROW_AVAILABLE, reason="pyarrow optional extra not installed")
def test_arrow_ipc_is_deterministic_and_sequence_sorted() -> None:
    table = rows_to_table(_rows())
    encoded = table_to_ipc(table)
    replay = table_from_ipc(encoded)

    assert replay.schema.metadata[b"seocho.schema_version"].decode() == PROJECTION_SCHEMA_VERSION
    assert replay.column("sequence").to_pylist() == [1, 2]
    assert table_to_ipc(rows_to_table(reversed(_rows()))) == encoded

    file_encoded = table_to_arrow_file(table)
    assert file_encoded.startswith(b"ARROW1")
    assert table_from_arrow_file(file_encoded).equals(table)
    assert file_encoded != encoded


@pytest.mark.skipif(not PYARROW_AVAILABLE, reason="pyarrow optional extra not installed")
def test_parquet_artifact_round_trip_has_auditable_receipt(tmp_path: Path) -> None:
    path = tmp_path / "projection.parquet"
    table = rows_to_table(_rows())
    receipt = write_parquet_artifact(table, path)
    replay = read_parquet_artifact(path)

    assert receipt.row_count == 2
    assert receipt.minimum_sequence == 1
    assert receipt.maximum_sequence == 2
    assert receipt.byte_count == path.stat().st_size
    assert len(receipt.content_sha256) == 64
    assert replay.equals(table)
