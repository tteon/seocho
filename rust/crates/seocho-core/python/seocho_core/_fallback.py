"""Pure-Python fallback implementations for seocho-core functions."""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Tuple


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        xf = float(x)
        yf = float(y)
        dot += xf * yf
        norm_a += xf * xf
        norm_b += yf * yf
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return max(min(dot / (math.sqrt(norm_a) * math.sqrt(norm_b)), 1.0), -1.0)


def cosine_similarity_matrix(vecs: List[List[float]]) -> List[List[float]]:
    """Compute NxN cosine similarity matrix."""
    n = len(vecs)
    if n == 0:
        return []
    norms = [math.sqrt(sum(x * x for x in v)) for v in vecs]
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        matrix[i][i] = 1.0
        for j in range(i + 1, n):
            if norms[i] <= 0.0 or norms[j] <= 0.0 or len(vecs[i]) != len(vecs[j]):
                continue
            dot = sum(a * b for a, b in zip(vecs[i], vecs[j]))
            sim = max(min(dot / (norms[i] * norms[j]), 1.0), -1.0)
            matrix[i][j] = sim
            matrix[j][i] = sim
    return matrix


def infer_rules_from_nodes(
    nodes_json: str,
    required_threshold: float = 0.98,
    enum_max_size: int = 20,
) -> str:
    """Infer rules from a JSON array of nodes. Returns JSON string of rules."""
    nodes = json.loads(nodes_json)
    buckets: Dict[Tuple[str, str], Dict[str, list]] = {}

    for node in nodes:
        label = (node.get("label") or "").strip()
        if not label:
            continue
        props = node.get("properties") or {}
        for key, value in props.items():
            bucket = buckets.setdefault(
                (label, key), {"values": [], "nonnull_values": []}
            )
            bucket["values"].append(value)
            if value is not None:
                bucket["nonnull_values"].append(value)

    rules: List[Dict[str, Any]] = []

    for (label, prop_name), stats in buckets.items():
        values = stats["values"]
        nonnull = stats["nonnull_values"]
        total = len(values)
        if total == 0:
            continue

        completeness = len(nonnull) / total
        if completeness >= required_threshold:
            rules.append(
                {
                    "label": label,
                    "property_name": prop_name,
                    "kind": "required",
                    "params": {"minCount": 1},
                }
            )

        dominant_type = _infer_dominant_type(nonnull)
        if dominant_type is not None:
            rules.append(
                {
                    "label": label,
                    "property_name": prop_name,
                    "kind": "datatype",
                    "params": {"datatype": dominant_type},
                }
            )

        unique_values = _dedupe(nonnull)
        if (
            0 < len(unique_values) <= enum_max_size
            and len(unique_values) <= max(2, int(total * 0.2))
        ):
            rules.append(
                {
                    "label": label,
                    "property_name": prop_name,
                    "kind": "enum",
                    "params": {"allowedValues": unique_values},
                }
            )

        min_max = _infer_numeric_range(nonnull)
        if min_max is not None:
            rules.append(
                {
                    "label": label,
                    "property_name": prop_name,
                    "kind": "range",
                    "params": {"minInclusive": min_max[0], "maxInclusive": min_max[1]},
                }
            )

    return json.dumps(rules)


def _infer_dominant_type(values: list) -> Optional[str]:
    if not values:
        return None
    counts: Dict[str, int] = {}
    for v in values:
        kind = _type_name(v)
        counts[kind] = counts.get(kind, 0) + 1
    return max(counts.items(), key=lambda item: item[1])[0]


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _infer_numeric_range(values: list) -> Optional[Tuple[float, float]]:
    nums = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not nums:
        return None
    return min(nums), max(nums)


def _dedupe(values: list) -> list:
    seen = set()
    result = []
    for v in values:
        marker = repr(v)
        if marker not in seen:
            seen.add(marker)
            result.append(v)
    return result
