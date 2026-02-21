from __future__ import annotations

import re
from typing import Any, Dict, List

from rule_constraints import RuleSet


def export_ruleset_to_cypher(rule_profile: Dict[str, Any]) -> Dict[str, Any]:
    ruleset = RuleSet.from_dict(rule_profile)
    statements: List[str] = []
    unsupported: List[Dict[str, Any]] = []
    seen = set()

    for rule in ruleset.rules:
        if rule.kind == "required":
            label = _safe_ident(rule.label)
            prop = _safe_ident(rule.property_name)
            cname = _safe_ident(f"rq_{label}_{prop}_not_null")
            stmt = (
                f"CREATE CONSTRAINT {cname} IF NOT EXISTS "
                f"FOR (n:{label}) REQUIRE n.{prop} IS NOT NULL"
            )
            if stmt not in seen:
                seen.add(stmt)
                statements.append(stmt)
            continue

        unsupported.append(
            {
                "label": rule.label,
                "property_name": rule.property_name,
                "kind": rule.kind,
                "reason": "no direct DozerDB schema constraint mapping for this rule kind",
            }
        )

    return {
        "schema_version": rule_profile.get("schema_version", "rules.v1"),
        "statements": statements,
        "unsupported_rules": unsupported,
    }


def export_ruleset_to_shacl(rule_profile: Dict[str, Any]) -> Dict[str, Any]:
    ruleset = RuleSet.from_dict(rule_profile)
    grouped: Dict[str, List[Any]] = {}
    for rule in ruleset.rules:
        grouped.setdefault(rule.label, []).append(rule)

    unsupported: List[Dict[str, Any]] = []
    shapes: List[Dict[str, Any]] = []
    ttl_lines: List[str] = [
        "@prefix sh: <http://www.w3.org/ns/shacl#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "@prefix ex: <https://seocho.local/shapes#> .",
        "",
    ]

    for label, rules in grouped.items():
        shape_name = f"{_safe_ident(label)}Shape"
        properties: List[Dict[str, Any]] = []
        ttl_blocks: List[List[str]] = []

        for rule in rules:
            block = _rule_to_shacl_block(rule)
            if block is None:
                unsupported.append(
                    {
                        "label": rule.label,
                        "property_name": rule.property_name,
                        "kind": rule.kind,
                        "reason": "unsupported SHACL mapping for rule kind",
                    }
                )
                continue

            ttl_blocks.append(block["ttl"])
            properties.append(
                {
                    "path": rule.property_name,
                    "constraint": rule.kind,
                    "params": dict(rule.params or {}),
                    "shacl_terms": block["terms"],
                }
            )

        shapes.append(
            {
                "shape_id": shape_name,
                "target_class": label,
                "properties": properties,
            }
        )
        ttl_lines.extend(_render_shape_turtle(shape_name=shape_name, label=label, property_blocks=ttl_blocks))
        ttl_lines.append("")

    return {
        "schema_version": rule_profile.get("schema_version", "rules.v1"),
        "shapes": shapes,
        "turtle": "\n".join(ttl_lines).strip() + "\n",
        "unsupported_rules": unsupported,
    }


def _safe_ident(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", str(name))
    if not cleaned:
        return "x"
    if cleaned[0].isdigit():
        cleaned = f"x_{cleaned}"
    return cleaned


def _rule_to_shacl_block(rule: Any) -> Dict[str, Any] | None:
    prop_ident = _safe_ident(rule.property_name)
    terms: Dict[str, Any] = {"sh:path": f"ex:{prop_ident}"}

    if rule.kind == "required":
        terms["sh:minCount"] = int(rule.params.get("minCount", 1))
    elif rule.kind == "datatype":
        dtype = str(rule.params.get("datatype", "string"))
        terms["sh:datatype"] = _datatype_to_xsd(dtype)
    elif rule.kind == "enum":
        allowed = list(rule.params.get("allowedValues", []))
        terms["sh:in"] = [_format_shacl_literal(value) for value in allowed]
    elif rule.kind == "range":
        if "minInclusive" in rule.params:
            terms["sh:minInclusive"] = _format_shacl_literal(rule.params["minInclusive"])
        if "maxInclusive" in rule.params:
            terms["sh:maxInclusive"] = _format_shacl_literal(rule.params["maxInclusive"])
    else:
        return None

    ttl_terms: List[str] = [f"sh:path ex:{prop_ident}"]
    if "sh:minCount" in terms:
        ttl_terms.append(f"sh:minCount {terms['sh:minCount']}")
    if "sh:datatype" in terms:
        ttl_terms.append(f"sh:datatype {terms['sh:datatype']}")
    if "sh:in" in terms:
        values = " ".join(terms["sh:in"])
        ttl_terms.append(f"sh:in ({values})")
    if "sh:minInclusive" in terms:
        ttl_terms.append(f"sh:minInclusive {terms['sh:minInclusive']}")
    if "sh:maxInclusive" in terms:
        ttl_terms.append(f"sh:maxInclusive {terms['sh:maxInclusive']}")

    return {"terms": terms, "ttl": ttl_terms}


def _render_shape_turtle(shape_name: str, label: str, property_blocks: List[List[str]]) -> List[str]:
    lines: List[str] = [
        f"ex:{shape_name} a sh:NodeShape ;",
        f"  sh:targetClass ex:{_safe_ident(label)}",
    ]
    if not property_blocks:
        lines[-1] = f"{lines[-1]} ."
        return lines

    lines[-1] = f"{lines[-1]} ;"
    for idx, block in enumerate(property_blocks):
        lines.append("  sh:property [")
        for term_idx, term in enumerate(block):
            suffix = " ;" if term_idx < len(block) - 1 else ""
            lines.append(f"    {term}{suffix}")
        block_suffix = " ;" if idx < len(property_blocks) - 1 else " ."
        lines.append(f"  ]{block_suffix}")
    return lines


def _datatype_to_xsd(datatype: str) -> str:
    normalized = datatype.strip().lower()
    if normalized in {"string", "str"}:
        return "xsd:string"
    if normalized in {"integer", "int"}:
        return "xsd:integer"
    if normalized in {"number", "float", "double", "decimal"}:
        return "xsd:decimal"
    if normalized in {"boolean", "bool"}:
        return "xsd:boolean"
    return "xsd:string"


def _format_shacl_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
