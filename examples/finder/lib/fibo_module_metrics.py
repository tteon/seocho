"""Metrics for the FinDER FIBO-module-impact tutorial.

Each helper takes an in-memory snapshot of the constructed graph (a list
of node dicts and a list of relationship dicts, the same shape ``Seocho``
returns from ``add()``) and computes one quality signal.

These are intentionally simple: the tutorial values transparency over
sophistication. Replace any of them with a richer scorer for production
work.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Sequence


_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def _normalize(s: str) -> str:
    return _NORMALIZE_RE.sub(" ", str(s).lower()).strip()


def graph_volume(nodes: Sequence[Dict[str, Any]], relationships: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """Distinct labels, distinct rel types, total counts."""
    distinct_labels = {str(n.get("label", "")) for n in nodes if n.get("label")}
    distinct_rel_types = {str(r.get("type", "")) for r in relationships if r.get("type")}
    return {
        "node_count": len(nodes),
        "relationship_count": len(relationships),
        "distinct_labels": len(distinct_labels),
        "distinct_rel_types": len(distinct_rel_types),
    }


def entity_coverage(
    nodes: Sequence[Dict[str, Any]],
    expected_entities: Iterable[str],
) -> Dict[str, Any]:
    """Fraction of an expected-entity list that appears in the graph by name."""
    present_names = {
        _normalize(n.get("properties", {}).get("name", n.get("id", "")))
        for n in nodes
    }
    expected = list(expected_entities)
    matched = [
        e for e in expected
        if any(_normalize(e) in name or name in _normalize(e) for name in present_names if name)
    ]
    return {
        "expected": len(expected),
        "matched": len(matched),
        "coverage": (len(matched) / len(expected)) if expected else 0.0,
        "missing": [e for e in expected if e not in matched],
    }


def label_distribution(nodes: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """Count of nodes per label."""
    counts: Dict[str, int] = {}
    for n in nodes:
        label = str(n.get("label", "Unknown"))
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))


def shacl_violation_count(
    nodes: Sequence[Dict[str, Any]],
    relationships: Sequence[Dict[str, Any]],
    ontology: Any,
) -> Dict[str, Any]:
    """Soft SHACL-style validation: required properties + label allowlist.

    Production validation should use ``Ontology.to_shacl()`` + ``pyshacl``
    against an RDF dump. This soft check operates on the in-memory
    nodes/relationships and surfaces the same kind of violations
    (missing required props, unknown labels) without the heavyweight
    triple conversion.
    """
    allowed_labels = set(getattr(ontology, "_allowed_labels", set()) or set(ontology.nodes.keys()))
    required_props_by_label: Dict[str, List[str]] = {}
    for label, node_def in ontology.nodes.items():
        required = []
        for pname, p in node_def.properties.items():
            if getattr(p, "required", False):
                required.append(pname)
        required_props_by_label[label] = required

    violations: List[str] = []
    for n in nodes:
        label = str(n.get("label", ""))
        if label and label not in allowed_labels:
            violations.append(f"unknown_label:{label}")
            continue
        props = n.get("properties", {})
        for required_prop in required_props_by_label.get(label, []):
            if not props.get(required_prop):
                violations.append(f"missing_required:{label}.{required_prop}")

    by_kind: Dict[str, int] = {}
    for v in violations:
        kind = v.split(":", 1)[0]
        by_kind[kind] = by_kind.get(kind, 0) + 1

    return {"total": len(violations), "by_kind": by_kind}


def qa_correctness(
    answers: Sequence[Dict[str, Any]],
) -> Dict[str, float]:
    """Aggregate exact-/contains-match rate across QA results.

    ``answers`` is a list of ``{"answer", "expected"}`` dicts.
    """
    if not answers:
        return {"contains_match_rate": 0.0, "exact_match_rate": 0.0, "n": 0}
    contains = 0
    exact = 0
    for a in answers:
        norm_a = _normalize(a.get("answer", ""))
        norm_e = _normalize(a.get("expected", ""))
        if norm_a == norm_e and norm_e:
            exact += 1
        if norm_e and norm_e in norm_a:
            contains += 1
    return {
        "contains_match_rate": contains / len(answers),
        "exact_match_rate": exact / len(answers),
        "n": len(answers),
    }
