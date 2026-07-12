"""Language-neutral Arrow/Parquet contract for memory projection batches."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


PROJECTION_SCHEMA_VERSION = "seocho.projection.arrow.v1"


def _arrow() -> tuple[Any, Any]:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ImportError("Arrow projection artifacts require 'pyarrow'") from exc
    return pa, pq


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ProjectionArtifactReceipt:
    schema_version: str
    row_count: int
    minimum_sequence: int
    maximum_sequence: int
    content_sha256: str
    byte_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "row_count": self.row_count,
            "minimum_sequence": self.minimum_sequence,
            "maximum_sequence": self.maximum_sequence,
            "content_sha256": self.content_sha256,
            "byte_count": self.byte_count,
        }


def projection_schema() -> Any:
    pa, _ = _arrow()
    metadata = {b"seocho.schema_version": PROJECTION_SCHEMA_VERSION.encode()}
    return pa.schema(
        [
            ("workspace_id", pa.string()),
            ("sequence", pa.int64()),
            ("memory_id", pa.string()),
            ("event_type", pa.string()),
            ("occurred_at", pa.string()),
            ("provenance_id", pa.string()),
            ("idempotency_key", pa.string()),
            ("memory_schema_version", pa.string()),
            ("payload_json", pa.large_string()),
            ("payload_sha256", pa.string()),
        ],
        metadata=metadata,
    )


def rows_to_table(rows: Iterable[Mapping[str, Any]]) -> Any:
    pa, _ = _arrow()
    normalized: list[dict[str, Any]] = []
    for row in rows:
        payload_json = _canonical_json(row.get("payload") or {})
        normalized.append(
            {
                "workspace_id": str(row.get("workspace_id") or ""),
                "sequence": int(row["sequence"]),
                "memory_id": str(row.get("memory_id") or ""),
                "event_type": str(row.get("event_type") or ""),
                "occurred_at": str(row.get("occurred_at") or ""),
                "provenance_id": str(row.get("provenance_id") or ""),
                "idempotency_key": str(row.get("idempotency_key") or ""),
                "memory_schema_version": str(row.get("schema_version") or ""),
                "payload_json": payload_json,
                "payload_sha256": hashlib.sha256(payload_json.encode()).hexdigest(),
            }
        )
    normalized.sort(key=lambda item: (item["workspace_id"], item["sequence"]))
    return pa.Table.from_pylist(normalized, schema=projection_schema())


def table_to_ipc(table: Any) -> bytes:
    pa, _ = _arrow()
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue().to_pybytes()


def table_from_ipc(payload: bytes) -> Any:
    pa, _ = _arrow()
    with pa.ipc.open_stream(pa.py_buffer(payload)) as reader:
        return reader.read_all()


def write_parquet_artifact(
    table: Any,
    path: Path,
    *,
    compression: str = "zstd",
) -> ProjectionArtifactReceipt:
    _, pq = _arrow()
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        table,
        path,
        compression=compression,
        use_dictionary=["event_type", "memory_schema_version"],
        write_statistics=True,
    )
    payload = path.read_bytes()
    sequences = table.column("sequence").to_pylist()
    return ProjectionArtifactReceipt(
        schema_version=PROJECTION_SCHEMA_VERSION,
        row_count=table.num_rows,
        minimum_sequence=min(sequences, default=0),
        maximum_sequence=max(sequences, default=0),
        content_sha256=hashlib.sha256(payload).hexdigest(),
        byte_count=len(payload),
    )


def read_parquet_artifact(path: Path) -> Any:
    _, pq = _arrow()
    table = pq.read_table(path)
    version = (table.schema.metadata or {}).get(b"seocho.schema_version", b"").decode()
    if version != PROJECTION_SCHEMA_VERSION:
        raise ValueError(f"Unsupported projection artifact schema: {version!r}")
    return table


__all__ = [
    "PROJECTION_SCHEMA_VERSION",
    "ProjectionArtifactReceipt",
    "projection_schema",
    "read_parquet_artifact",
    "rows_to_table",
    "table_from_ipc",
    "table_to_ipc",
    "write_parquet_artifact",
]
