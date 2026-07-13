"""Normalized connector records for bringing external data into SEOCHO.

Connectors materialize source data as JSONL records that the existing file
indexer can consume. This keeps live API concerns outside the indexing core:

    external API / loader -> ConnectorRecord -> .jsonl -> seocho run
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

SCHEMA_VERSION = "seocho.connector_record.v1"
_SECRET_KEY_HINTS = ("authorization", "cookie", "password", "secret", "token")


def content_sha256(content: str) -> str:
    """Return a stable sha256 for content-bearing records."""

    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def stable_record_id(provider: str, external_id: str, content: str = "") -> str:
    """Build a deterministic record id.

    ``external_id`` wins when present. For anonymous loader documents, fall back
    to the content hash so repeated exports are idempotent.
    """

    source = str(external_id or "").strip() or content_sha256(content)[:24]
    return f"{provider}:{source}"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _sanitize_value(key: str, value: Any) -> Any:
    if any(hint in key.lower() for hint in _SECRET_KEY_HINTS):
        return "[redacted]"
    if isinstance(value, Mapping):
        return {str(child_key): _sanitize_value(str(child_key), child_value) for child_key, child_value in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_value("", item) for item in value]
    return _json_safe(value)


def sanitize_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Return JSON-safe metadata with obvious credential fields redacted."""

    sanitized: dict[str, Any] = {}
    for key, value in metadata.items():
        text_key = str(key)
        sanitized[text_key] = _sanitize_value(text_key, value)
    return sanitized


@dataclass(slots=True)
class ConnectorRecord:
    """A source document normalized for SEOCHO's existing JSON/JSONL readers."""

    id: str
    content: str
    provider: str
    source_kind: str
    category: str = "connector"
    title: str = ""
    url: str = ""
    source_type: str = "text"
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        content = str(self.content or "")
        metadata = sanitize_metadata(self.metadata)
        metadata.update(
            {
                "provider": self.provider,
                "source_kind": self.source_kind,
                "schema_version": SCHEMA_VERSION,
                "content_sha256": content_sha256(content),
            }
        )
        payload: dict[str, Any] = {
            "id": self.id,
            "content": content,
            "category": self.category,
            "source_type": self.source_type,
            "metadata": metadata,
        }
        if self.title:
            payload["title"] = self.title
        if self.url:
            payload["url"] = self.url
        if self.created_at:
            payload["created_at"] = self.created_at
        if self.updated_at:
            payload["updated_at"] = self.updated_at
        return payload


def coerce_record(value: "ConnectorRecord | Mapping[str, Any]") -> ConnectorRecord:
    """Coerce a mapping back into ``ConnectorRecord`` for JSONL round trips."""

    if isinstance(value, ConnectorRecord):
        return value
    metadata = value.get("metadata") if isinstance(value.get("metadata"), Mapping) else {}
    provider = str(metadata.get("provider") or value.get("provider") or "external")
    content = str(value.get("content") or "")
    record_id = str(value.get("id") or stable_record_id(provider, "", content))
    return ConnectorRecord(
        id=record_id,
        content=content,
        provider=provider,
        source_kind=str(metadata.get("source_kind") or value.get("source_kind") or "document"),
        category=str(value.get("category") or "connector"),
        title=str(value.get("title") or ""),
        url=str(value.get("url") or ""),
        source_type=str(value.get("source_type") or "text"),
        created_at=str(value.get("created_at") or ""),
        updated_at=str(value.get("updated_at") or ""),
        metadata=dict(metadata),
    )


def write_records_jsonl(
    records: Iterable["ConnectorRecord | Mapping[str, Any]"],
    path: "str | Path",
) -> int:
    """Write records as UTF-8 JSONL and return the number written."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as handle:
        for record in records:
            item = coerce_record(record).to_dict()
            if not str(item.get("content") or "").strip():
                continue
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def read_records_jsonl(path: "str | Path") -> Iterator[ConnectorRecord]:
    """Read a connector JSONL file produced by ``write_records_jsonl``."""

    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, Mapping):
                yield coerce_record(payload)


def _doc_id(doc: Any, metadata: Mapping[str, Any], *, index: int) -> str:
    for attr in ("id", "id_", "doc_id", "node_id"):
        value = getattr(doc, attr, None)
        if value:
            return str(value)
    for key in ("id", "source", "file_path", "path", "url"):
        value = metadata.get(key)
        if value:
            return str(value)
    return str(index)


def records_from_langchain_documents(
    documents: Iterable[Any],
    *,
    category: str = "langchain",
    provider: str = "langchain",
) -> list[ConnectorRecord]:
    """Convert LangChain-like ``Document`` objects by duck typing.

    No LangChain dependency is imported; any object with ``page_content`` and
    optional ``metadata`` / ``id`` is accepted.
    """

    records: list[ConnectorRecord] = []
    for index, doc in enumerate(documents):
        content = str(getattr(doc, "page_content", "") or "")
        if not content.strip():
            continue
        metadata = dict(getattr(doc, "metadata", {}) or {})
        external_id = _doc_id(doc, metadata, index=index)
        metadata.setdefault("framework", "langchain")
        metadata.setdefault("external_id", external_id)
        title = str(metadata.get("title") or metadata.get("source") or "")
        records.append(
            ConnectorRecord(
                id=stable_record_id(provider, external_id, content),
                content=content,
                provider=provider,
                source_kind="langchain_document",
                category=category,
                title=title,
                url=str(metadata.get("url") or metadata.get("source_url") or ""),
                metadata=metadata,
            )
        )
    return records


def records_from_llamaindex_documents(
    documents: Iterable[Any],
    *,
    category: str = "llamaindex",
    provider: str = "llamaindex",
) -> list[ConnectorRecord]:
    """Convert LlamaIndex-like ``Document`` or ``Node`` objects by duck typing."""

    records: list[ConnectorRecord] = []
    for index, doc in enumerate(documents):
        get_content = getattr(doc, "get_content", None)
        if callable(get_content):
            content = str(get_content() or "")
        else:
            content = str(getattr(doc, "text", "") or "")
        if not content.strip():
            continue
        metadata = dict(getattr(doc, "metadata", {}) or {})
        external_id = _doc_id(doc, metadata, index=index)
        metadata.setdefault("framework", "llamaindex")
        metadata.setdefault("external_id", external_id)
        records.append(
            ConnectorRecord(
                id=stable_record_id(provider, external_id, content),
                content=content,
                provider=provider,
                source_kind="llamaindex_document",
                category=category,
                title=str(metadata.get("title") or metadata.get("source") or ""),
                url=str(metadata.get("url") or metadata.get("source_url") or ""),
                metadata=metadata,
            )
        )
    return records


def summarize_records(records: Iterable["ConnectorRecord | Mapping[str, Any]"]) -> dict[str, Any]:
    """Return a small, content-free summary for CLI output and tests."""

    by_provider: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    count = 0
    for record in records:
        item = coerce_record(record)
        count += 1
        by_provider[item.provider] = by_provider.get(item.provider, 0) + 1
        by_kind[item.source_kind] = by_kind.get(item.source_kind, 0) + 1
    return {"records": count, "providers": by_provider, "source_kinds": by_kind}


__all__ = [
    "ConnectorRecord",
    "SCHEMA_VERSION",
    "coerce_record",
    "content_sha256",
    "read_records_jsonl",
    "records_from_langchain_documents",
    "records_from_llamaindex_documents",
    "sanitize_metadata",
    "stable_record_id",
    "summarize_records",
    "write_records_jsonl",
]
