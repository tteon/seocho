from __future__ import annotations

import re
from typing import Any, Dict, List

from .contracts import CypherPlan


FORBIDDEN_CYPHER_TOKENS = (
    " CREATE ",
    " MERGE ",
    " DELETE ",
    " DETACH ",
    " SET ",
    " REMOVE ",
    " DROP ",
    " LOAD CSV ",
    " CALL DBMS",
    " CALL GDS",
)


class CypherQueryValidator:
    """Validate constrained Cypher plans before execution."""

    def validate(self, plan: CypherPlan, constraint_slice: Dict[str, Any]) -> Dict[str, Any]:
        normalized_query = " " + re.sub(r"\s+", " ", plan.query.upper()) + " "
        violations: List[str] = []
        if "$node_id" not in plan.query:
            violations.append("missing_node_binding")
        if "RETURN" not in normalized_query:
            violations.append("missing_return_clause")
        for token in FORBIDDEN_CYPHER_TOKENS:
            if token in normalized_query:
                violations.append(f"forbidden_token:{token.strip().lower().replace(' ', '_')}")

        labels = {
            match
            for match in re.findall(r"\([^)]+:([A-Za-z_][A-Za-z0-9_]*)", plan.query)
            if match
        }
        relation_types = {
            match
            for match in re.findall(r"\[[^\]]*:\s*([A-Za-z_][A-Za-z0-9_]*)", plan.query)
            if match
        }
        properties = {
            match
            for match in re.findall(r"[A-Za-z_][A-Za-z0-9_]*\.([A-Za-z_][A-Za-z0-9_]*)", plan.query)
            if match
        }

        allowed_labels = set(constraint_slice.get("allowed_labels", []))
        if allowed_labels and labels - allowed_labels:
            violations.append("unknown_labels:" + ",".join(sorted(labels - allowed_labels)))

        allowed_relationship_types = set(constraint_slice.get("allowed_relationship_types", []))
        if allowed_relationship_types and relation_types - allowed_relationship_types:
            violations.append(
                "unknown_relationship_types:" + ",".join(sorted(relation_types - allowed_relationship_types))
            )

        allowed_properties = set(constraint_slice.get("allowed_properties", []))
        if allowed_properties and properties - allowed_properties:
            violations.append("unknown_properties:" + ",".join(sorted(properties - allowed_properties)))

        return {
            "ok": not violations,
            "violations": violations,
            "labels": sorted(labels),
            "relation_types": sorted(relation_types),
            "properties": sorted(properties),
        }
