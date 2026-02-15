"""
Pure helper functions to build ontology hints payload.

This module intentionally avoids owlready2 dependency so it can be unit tested.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Sequence, Set


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def keyword_tokens(value: str) -> Set[str]:
    normalized = normalize_text(value)
    if not normalized:
        return set()
    return {token for token in normalized.split(" ") if len(token) >= 2}


def build_hints_from_records(records: Sequence[Dict[str, object]]) -> Dict[str, object]:
    aliases: Dict[str, str] = {}
    label_keywords: Dict[str, Set[str]] = {}

    for row in records:
        canonical = str(row.get("canonical", "")).strip()
        if not canonical:
            continue

        canonical_key = normalize_text(canonical)
        alias_values = set()
        for raw in row.get("aliases", []):  # type: ignore[arg-type]
            text = str(raw).strip()
            if text:
                alias_values.add(text)
        alias_values.add(canonical)

        for alias in alias_values:
            alias_key = normalize_text(alias)
            if alias_key:
                aliases[alias_key] = canonical

        existing_keywords = label_keywords.get(canonical_key, set())
        for raw in row.get("keywords", []):  # type: ignore[arg-type]
            existing_keywords.update(keyword_tokens(str(raw)))
        if canonical:
            existing_keywords.update(keyword_tokens(canonical))
        if existing_keywords:
            label_keywords[canonical_key] = existing_keywords

    return {
        "aliases": aliases,
        "label_keywords": {
            key: sorted(list(values))
            for key, values in label_keywords.items()
        },
    }

