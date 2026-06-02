"""
SHACL-like rule inference and validation for extracted graph data.

This is the **canonical** implementation shared by both the SDK (local mode)
and the extraction server.  ``extraction/rule_constraints.py`` re-exports
from this module for backward compatibility.

Usage::

    from seocho.rules import infer_rules_from_graph, apply_rules_to_graph

    ruleset = infer_rules_from_graph(extracted_data)
    annotated = apply_rules_to_graph(extracted_data, ruleset)
"""

from __future__ import annotations

import json as _json
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Optional Rust acceleration via seocho-core
try:
    from seocho_core import infer_rules_from_nodes as _native_infer

    _USE_NATIVE_RULES = True
except ImportError:
    _USE_NATIVE_RULES = False


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Rule:
    """A single inferred constraint (required, datatype, enum, or range)."""

    label: str
    property_name: str
    kind: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RuleSet:
    """Collection of inferred rules with serialization helpers.

    Phase 5 added ``ontology_identity_hash`` so a stored rule profile can
    be compared against the active ontology's
    ``OntologyContextDescriptor.context_hash`` before being applied.
    Empty string means "no hash recorded" (backward compatible with
    pre-Phase-5 dicts and the unset-default path).
    """

    schema_version: str = "rules.v1"
    rules: List[Rule] = field(default_factory=list)
    ontology_identity_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a portable dict (``schema_version`` + ``rules`` list)."""
        return {
            "schema_version": self.schema_version,
            "rules": [asdict(rule) for rule in self.rules],
            "ontology_identity_hash": self.ontology_identity_hash,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "RuleSet":
        """Deserialize from a dict produced by :meth:`to_dict`."""
        schema_version = payload.get("schema_version", "rules.v1")
        rules = []
        for item in payload.get("rules", []):
            rules.append(
                Rule(
                    label=item["label"],
                    property_name=item["property_name"],
                    kind=item["kind"],
                    params=item.get("params", {}),
                )
            )
        return cls(
            schema_version=schema_version,
            rules=rules,
            ontology_identity_hash=str(payload.get("ontology_identity_hash", "") or ""),
        )

    def to_shacl_like(self) -> Dict[str, Any]:
        """Return a SHACL-inspired shape document."""
        shapes: Dict[str, Dict[str, Any]] = {}
        for rule in self.rules:
            shape = shapes.setdefault(rule.label, {"targetClass": rule.label, "properties": []})
            shape["properties"].append(
                {
                    "path": rule.property_name,
                    "constraint": rule.kind,
                    "params": rule.params,
                }
            )
        return {"schema_version": self.schema_version, "shapes": list(shapes.values())}


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------


def infer_rules_from_graph(
    extracted_data: Dict[str, Any],
    required_threshold: float = 0.98,
    enum_max_size: int = 20,
    *,
    ontology_identity_hash: str = "",
) -> RuleSet:
    """Infer SHACL-like constraints from extracted node properties.

    Scans all nodes in *extracted_data* and produces rules for property
    completeness (required), dominant data type, enum membership, and
    numeric range.

    Args:
        extracted_data: Dict with ``"nodes"`` list (each node has
            ``label`` and ``properties``).
        required_threshold: Minimum non-null ratio (0–1) to emit a
            ``required`` rule.
        enum_max_size: Maximum distinct values to emit an ``enum`` rule.
        ontology_identity_hash: Optional ontology context hash (typically
            ``OntologyContextDescriptor.context_hash``) stamped onto the
            resulting :class:`RuleSet`. Phase 5: lets downstream rule
            profile storage refuse application across an ontology
            version change. Empty string preserves the legacy
            no-hash-recorded path.

    Returns:
        A :class:`RuleSet` with the inferred rules.
    """
    if _USE_NATIVE_RULES:
        nodes_json = _json.dumps(extracted_data.get("nodes", []))
        rules_json = _native_infer(nodes_json, required_threshold, enum_max_size)
        rules_list = _json.loads(rules_json)
        return RuleSet(
            rules=[Rule(**r) for r in rules_list],
            ontology_identity_hash=str(ontology_identity_hash or ""),
        )

    buckets: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for node in extracted_data.get("nodes", []):
        label = node.get("label")
        if not label:
            continue
        props = node.get("properties", {})
        for key, value in props.items():
            bucket = buckets.setdefault(
                (label, key),
                {"values": [], "nonnull_values": []},
            )
            bucket["values"].append(value)
            if value is not None:
                bucket["nonnull_values"].append(value)

    ruleset = RuleSet(ontology_identity_hash=str(ontology_identity_hash or ""))
    for (label, prop_name), stats in buckets.items():
        values = stats["values"]
        nonnull = stats["nonnull_values"]
        total = len(values)
        if total == 0:
            continue

        completeness = len(nonnull) / total
        if completeness >= required_threshold:
            ruleset.rules.append(
                Rule(
                    label=label,
                    property_name=prop_name,
                    kind="required",
                    params={"minCount": 1},
                )
            )

        dominant_type = _infer_dominant_type(nonnull)
        if dominant_type is not None:
            ruleset.rules.append(
                Rule(
                    label=label,
                    property_name=prop_name,
                    kind="datatype",
                    params={"datatype": dominant_type},
                )
            )

        unique_values = _dedupe_nonhashable(nonnull)
        if 0 < len(unique_values) <= enum_max_size and len(unique_values) <= max(2, int(total * 0.2)):
            ruleset.rules.append(
                Rule(
                    label=label,
                    property_name=prop_name,
                    kind="enum",
                    params={"allowedValues": unique_values},
                )
            )

        min_max = _infer_numeric_range(nonnull)
        if min_max is not None:
            min_value, max_value = min_max
            ruleset.rules.append(
                Rule(
                    label=label,
                    property_name=prop_name,
                    kind="range",
                    params={"minInclusive": min_value, "maxInclusive": max_value},
                )
            )

    return ruleset


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def apply_rules_to_graph(
    extracted_data: Dict[str, Any],
    ruleset: RuleSet,
) -> Dict[str, Any]:
    """Apply rules and annotate each node with validation results.

    Returns a deep copy of *extracted_data* with per-node
    ``rule_validation`` annotations and a ``rule_validation_summary``.
    """
    output = deepcopy(extracted_data)
    rules_by_label: Dict[str, List[Rule]] = {}
    for rule in ruleset.rules:
        rules_by_label.setdefault(rule.label, []).append(rule)

    failed_nodes = 0
    for node in output.get("nodes", []):
        label = node.get("label")
        props = node.get("properties", {})
        violations: List[Dict[str, Any]] = []

        for rule in rules_by_label.get(label, []):
            value = props.get(rule.property_name)
            violation = _validate_value(rule, value)
            if violation is not None:
                violations.append(violation)

        status = "pass" if not violations else "fail"
        if status == "fail":
            failed_nodes += 1

        node["rule_validation"] = {"status": status, "violations": violations}

    output["rule_profile"] = ruleset.to_dict()
    output["rule_validation_summary"] = {
        "total_nodes": len(output.get("nodes", [])),
        "failed_nodes": failed_nodes,
        "passed_nodes": len(output.get("nodes", [])) - failed_nodes,
    }
    return output


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _infer_dominant_type(values: List[Any]) -> Optional[str]:
    if not values:
        return None
    counts: Dict[str, int] = {}
    for value in values:
        kind = _type_name(value)
        counts[kind] = counts.get(kind, 0) + 1
    return max(counts.items(), key=lambda item: item[1])[0]


def _infer_numeric_range(values: List[Any]) -> Optional[Tuple[float, float]]:
    numeric_values: List[float] = []
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            numeric_values.append(float(value))
    if not numeric_values:
        return None
    return min(numeric_values), max(numeric_values)


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _dedupe_nonhashable(values: List[Any]) -> List[Any]:
    seen = set()
    ordered: List[Any] = []
    for value in values:
        marker = repr(value)
        if marker in seen:
            continue
        seen.add(marker)
        ordered.append(value)
    return ordered


def _validate_value(rule: Rule, value: Any) -> Optional[Dict[str, Any]]:
    if rule.kind == "required":
        if value is None or value == "":
            return {"rule": rule.kind, "property": rule.property_name, "message": "missing required value"}
        return None

    if value is None:
        return None

    if rule.kind == "datatype":
        actual = _type_name(value)
        expected = rule.params.get("datatype")
        if actual != expected:
            return {
                "rule": rule.kind,
                "property": rule.property_name,
                "message": f"type mismatch: expected {expected}, got {actual}",
            }
        return None

    if rule.kind == "enum":
        allowed = rule.params.get("allowedValues", [])
        if value not in allowed:
            return {
                "rule": rule.kind,
                "property": rule.property_name,
                "message": "value not in allowed enum set",
            }
        return None

    if rule.kind == "range":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return {
                "rule": rule.kind,
                "property": rule.property_name,
                "message": "non-numeric value for numeric range constraint",
            }
        min_value = float(rule.params.get("minInclusive"))
        max_value = float(rule.params.get("maxInclusive"))
        numeric = float(value)
        if numeric < min_value or numeric > max_value:
            return {
                "rule": rule.kind,
                "property": rule.property_name,
                "message": f"value out of range [{min_value}, {max_value}]",
            }
        return None

    return None
