"""Lineage contract for layered ontology and agent evaluation.

An upstream answer benchmark is not automatically a gold graph or a gold
Text2Cypher workload.  This module keeps those label layers separate while
letting a reviewed case connect them by stable references.  Missing labels are
explicitly unannotated and never silently scored as failures or successes.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Iterable, Mapping


CASE_ENVELOPE_SCHEMA_VERSION = "seocho.evaluation_case_envelope.v1"
_LABEL_STATES = {"reviewed", "unannotated", "unavailable"}
_LAYERS = ("ontology", "triples", "query", "answer", "governance")


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _items(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def validate_case_envelope(case: Mapping[str, Any]) -> list[str]:
    """Return deterministic errors for a content-local evaluation case.

    The envelope deliberately validates *label availability*, not the truth of
    a human annotation.  Review provenance belongs in the local gold artifact.
    """
    errors: list[str] = []
    if case.get("schema_version") != CASE_ENVELOPE_SCHEMA_VERSION:
        errors.append("schema_version must be seocho.evaluation_case_envelope.v1")
    if not isinstance(case.get("case_id"), str) or not case["case_id"].strip():
        errors.append("case_id must be a non-empty string")
    source = case.get("source")
    if not isinstance(source, Mapping) or not isinstance(source.get("snapshot_sha256"), str):
        errors.append("source.snapshot_sha256 must identify the pinned source snapshot")
    layers = case.get("layers")
    if not isinstance(layers, Mapping):
        return [*errors, "layers must be an object"]
    for name in _LAYERS:
        layer = layers.get(name)
        if not isinstance(layer, Mapping):
            errors.append(f"layers.{name} must be an object")
            continue
        status = layer.get("status")
        if status not in _LABEL_STATES:
            errors.append(f"layers.{name}.status must be reviewed, unannotated, or unavailable")
            continue
        if status != "reviewed":
            continue
        if name == "ontology" and not _items(layer.get("required_terms")):
            errors.append("reviewed ontology layer requires required_terms")
        elif name == "triples":
            if not _items(layer.get("gold_triples")):
                errors.append("reviewed triples layer requires gold_triples")
            if not _items(layer.get("source_bindings")):
                errors.append("reviewed triples layer requires source_bindings")
        elif name == "query":
            if not _items(layer.get("required_slots")):
                errors.append("reviewed query layer requires required_slots")
            if not _items(layer.get("expected_result_ids")):
                errors.append("reviewed query layer requires expected_result_ids")
        elif name == "answer" and "expected_answer_ref" not in layer:
            errors.append("reviewed answer layer requires expected_answer_ref")
        elif name == "governance" and not _items(layer.get("variants")):
            errors.append("reviewed governance layer requires variants")
    return errors


def case_receipt(case: Mapping[str, Any]) -> dict[str, Any]:
    """Produce a content-free receipt linking results to annotation coverage."""
    errors = validate_case_envelope(case)
    layers = case.get("layers") if isinstance(case.get("layers"), Mapping) else {}
    statuses = {
        name: dict(layers.get(name, {})).get("status", "invalid") for name in _LAYERS
    }
    return {
        "schema_version": "seocho.evaluation_case_receipt.v1",
        "case_id": case.get("case_id"),
        "case_sha256": _digest(case),
        "source_snapshot_sha256": dict(case.get("source", {})).get("snapshot_sha256"),
        "layer_status": statuses,
        "scorable_layers": sorted(
            name for name, status in statuses.items() if status == "reviewed"
        ),
        "valid": not errors,
        "errors": errors,
    }


def annotation_coverage(cases: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize label availability without fabricating missing gold scores."""
    rows = list(cases)
    counts: dict[str, Counter[str]] = {name: Counter() for name in _LAYERS}
    invalid = 0
    for case in rows:
        receipt = case_receipt(case)
        invalid += not bool(receipt["valid"])
        for name, status in receipt["layer_status"].items():
            counts[name][str(status)] += 1
    return {
        "schema_version": "seocho.evaluation_annotation_coverage.v1",
        "case_count": len(rows),
        "invalid_case_count": invalid,
        "layers": {
            name: {
                "reviewed": counts[name]["reviewed"],
                "unannotated": counts[name]["unannotated"],
                "unavailable": counts[name]["unavailable"],
                "coverage_rate": round(counts[name]["reviewed"] / len(rows), 6)
                if rows
                else 0.0,
            }
            for name in _LAYERS
        },
    }


__all__ = [
    "CASE_ENVELOPE_SCHEMA_VERSION",
    "annotation_coverage",
    "case_receipt",
    "validate_case_envelope",
]
