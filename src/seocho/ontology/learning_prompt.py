"""Stable prompt contracts for review-only ontology-learning experiments.

The two arms deliberately share one JSON contract. This makes an ablation about
framing rather than an accidental comparison between incompatible output formats.
Candidates remain untrusted until normal governance approval accepts a revision.
"""

from __future__ import annotations

from typing import Any, Mapping


ARMS = ("basic", "llms4ol")

CANDIDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["terms", "taxonomy", "relations", "axioms"],
    "properties": {
        "terms": {"type": "array", "items": {"type": "object"}},
        "taxonomy": {"type": "array", "items": {"type": "object"}},
        "relations": {"type": "array", "items": {"type": "object"}},
        "axioms": {"type": "array", "items": {"type": "object"}},
    },
}

_SYSTEM = """You are an ontology-learning assistant. Return one JSON object only.
Never claim that a candidate is approved or canonical. Use only information in
the supplied source. When a proposed item lacks a supporting source excerpt,
omit it rather than inventing it.

Use exactly this top-level shape:
{"terms": [{"term": string, "type": string, "evidence": string}],
 "taxonomy": [{"child": string, "parent": string, "evidence": string}],
 "relations": [{"source_type": string, "predicate": string,
                "target_type": string, "evidence": string}],
 "axioms": [{"kind": string, "statement": string, "evidence": string}]}
"""

_BASIC = """Extract a compact candidate ontology from the source: terms and their
types, is-a taxonomy edges, non-is-a relation signatures, and constraints.
Every item needs a short exact supporting excerpt in evidence. Return empty
arrays for categories not supported by the source.
"""

_LLMS4OL = """Apply the LLMs4OL ontology-learning decomposition in this order:
1. terminology extraction and term typing;
2. type-taxonomy discovery (only defensible is-a edges);
3. non-taxonomic relation extraction (typed source, predicate, typed target);
4. axiom discovery only when the source supports a constraint.

Keep lexical terms separate from conceptual types. Do not turn examples or
instances into universal taxonomy claims. Every candidate needs a short exact
supporting excerpt in evidence. Return empty arrays for unsupported categories.
"""


def prompt_for_arm(arm: str, source: str) -> tuple[str, str]:
    """Return the stable system/user prompts for one bounded ablation arm."""
    if arm not in ARMS:
        raise ValueError(f"unsupported prompt arm: {arm}")
    instruction = _BASIC if arm == "basic" else _LLMS4OL
    return _SYSTEM, f"{instruction}\n\nSOURCE:\n{source}"


def normalize_candidates(payload: Mapping[str, Any]) -> dict[str, list[dict[str, str]]]:
    """Normalize model output without silently accepting a different contract."""
    normalized: dict[str, list[dict[str, str]]] = {}
    for key in ("terms", "taxonomy", "relations", "axioms"):
        raw = payload.get(key, [])
        if not isinstance(raw, list):
            raise ValueError(f"{key} must be an array")
        items: list[dict[str, str]] = []
        for value in raw:
            if not isinstance(value, Mapping):
                raise ValueError(f"{key} entries must be objects")
            items.append(
                {str(k): str(v).strip() for k, v in value.items() if v is not None}
            )
        normalized[key] = items
    return normalized


def candidate_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return content-free quality diagnostics for an unapproved candidate set."""
    normalized = normalize_candidates(payload)
    all_items = [item for items in normalized.values() for item in items]
    evidence_present = sum(bool(item.get("evidence")) for item in all_items)
    return {
        "candidate_counts": {key: len(value) for key, value in normalized.items()},
        "candidate_total": len(all_items),
        "evidence_coverage": evidence_present / len(all_items) if all_items else 1.0,
    }
