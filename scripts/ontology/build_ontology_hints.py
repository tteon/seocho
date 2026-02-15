#!/usr/bin/env python3
"""
Build ontology hints JSON from an OWL ontology file using owlready2.

Output file is consumed by extraction/ontology_hints.py at query time.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Sequence


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "extraction"))

from ontology_hints_builder import build_hints_from_records, keyword_tokens  # noqa: E402


ANNOTATION_FIELDS = (
    "label",
    "comment",
    "altLabel",
    "prefLabel",
    "hasExactSynonym",
    "hasRelatedSynonym",
)


def _to_list(value: object) -> List[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _annotation_values(entity: object) -> List[str]:
    values: List[str] = []
    for attr in ANNOTATION_FIELDS:
        if not hasattr(entity, attr):
            continue
        for raw in _to_list(getattr(entity, attr)):
            text = str(raw).strip()
            if text:
                values.append(text)
    return values


def _canonical_name(entity: object) -> str:
    labels = _annotation_values(entity)
    if labels:
        return labels[0]
    return str(getattr(entity, "name", "")).strip()


def _entity_record(entity: object, kind: str) -> Dict[str, object]:
    name = str(getattr(entity, "name", "")).strip()
    annotations = _annotation_values(entity)
    canonical = _canonical_name(entity) or name

    aliases = set()
    if name:
        aliases.add(name)
    for item in annotations:
        aliases.add(item)

    keywords = set()
    if name:
        keywords.update(keyword_tokens(name))
    for item in annotations:
        keywords.update(keyword_tokens(item))
    keywords.add(kind)

    return {
        "canonical": canonical,
        "aliases": sorted(aliases),
        "keywords": sorted(keywords),
    }


def extract_records(ontology: object) -> List[Dict[str, object]]:
    records: List[Dict[str, object]] = []

    for cls in ontology.classes():
        records.append(_entity_record(cls, "class"))
    for individual in ontology.individuals():
        records.append(_entity_record(individual, "individual"))
    for prop in ontology.properties():
        records.append(_entity_record(prop, "property"))

    return records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build ontology hints JSON from OWL file.")
    parser.add_argument("--ontology", required=True, help="Path or URL to ontology file.")
    parser.add_argument(
        "--output",
        default="output/ontology_hints.json",
        help="Output JSON path (default: output/ontology_hints.json).",
    )
    parser.add_argument(
        "--merge-existing",
        action="store_true",
        help="Merge with existing output file instead of overwrite.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        from owlready2 import get_ontology
    except Exception as exc:
        print(
            "owlready2 is required for this script. Install with: pip install owlready2",
            file=sys.stderr,
        )
        print(f"Import error: {exc}", file=sys.stderr)
        return 2

    onto = get_ontology(args.ontology).load()
    records = extract_records(onto)
    payload = build_hints_from_records(records)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.merge_existing and output_path.exists():
        with output_path.open("r", encoding="utf-8") as handle:
            previous = json.load(handle)
        merged_records: List[Dict[str, object]] = []
        for alias, canonical in previous.get("aliases", {}).items():
            merged_records.append(
                {
                    "canonical": canonical,
                    "aliases": [alias, canonical],
                    "keywords": previous.get("label_keywords", {}).get(alias, []),
                }
            )
        merged_records.extend(records)
        payload = build_hints_from_records(merged_records)

    payload["metadata"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ontology_source": args.ontology,
        "record_count": len(records),
    }

    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    print(f"Wrote ontology hints: {output_path}")
    print(f"Aliases: {len(payload.get('aliases', {}))}")
    print(f"Label groups: {len(payload.get('label_keywords', {}))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

