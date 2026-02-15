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


def _safe_ident(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", str(name))
    if not cleaned:
        return "x"
    if cleaned[0].isdigit():
        cleaned = f"x_{cleaned}"
    return cleaned
