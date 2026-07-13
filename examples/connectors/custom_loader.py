"""Custom connector starter for SEOCHO.

This example has no external service dependency. Replace ``load_events`` with
your SaaS/API/database reader and keep emitting ``ConnectorRecord`` objects.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from seocho.connectors import ConnectorRecord, write_records_jsonl


def load_events(path: Path) -> list[dict[str, Any]]:
    """Load a tiny fixture that stands in for a SaaS/API response."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON array")
    return [dict(item) for item in payload if isinstance(item, Mapping)]


def events_to_records(events: Iterable[Mapping[str, Any]]) -> list[ConnectorRecord]:
    """Convert source events into SEOCHO connector records."""

    records: list[ConnectorRecord] = []
    for event in events:
        event_id = str(event.get("id") or "").strip()
        content = str(event.get("content") or "").strip()
        if not event_id or not content:
            continue

        metadata = {
            "external_id": event_id,
            "source_status": event.get("status", ""),
            "source_owner": event.get("owner", ""),
            "source_labels": event.get("labels", []),
            "source_url": event.get("url", ""),
        }
        records.append(
            ConnectorRecord(
                id=f"custom_saas:{event_id}",
                content=content,
                provider="custom_saas",
                source_kind=str(event.get("kind") or "custom_event"),
                category=str(event.get("category") or "connector_starter"),
                title=str(event.get("title") or event_id),
                url=str(event.get("url") or ""),
                created_at=str(event.get("created_at") or ""),
                updated_at=str(event.get("updated_at") or ""),
                metadata=metadata,
            )
        )
    return records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write SEOCHO connector JSONL from a local fixture")
    parser.add_argument(
        "--input",
        default="examples/connectors/fixtures/custom_saas_events.json",
        help="Input fixture path",
    )
    parser.add_argument(
        "--output",
        default=".seocho/connectors/custom_saas.jsonl",
        help="Output JSONL path",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    records = events_to_records(load_events(Path(args.input)))
    count = write_records_jsonl(records, args.output)
    print(f"wrote {count} connector records to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

