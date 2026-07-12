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
        if re.search(r"\[[^\]]*\*\s*(?:\]|\.\.\s*\])", plan.query):
            violations.append("unbounded_graph_path")
        max_graph_hops = int(constraint_slice.get("max_graph_hops", 0) or 0)
        if max_graph_hops:
            for _lower, upper in re.findall(r"\*(\d+)\.\.(\d+)", plan.query):
                if int(upper) > max_graph_hops:
                    violations.append("graph_hop_limit_exceeded")
                    break
        max_result_rows = int(constraint_slice.get("max_result_rows", 0) or 0)
        if max_result_rows:
            limit_match = re.search(r"\bLIMIT\s+(\$[A-Za-z_][A-Za-z0-9_]*|\d+)", plan.query, re.IGNORECASE)
            if limit_match is None:
                violations.append("missing_result_limit")
            else:
                limit_token = limit_match.group(1)
                if limit_token.startswith("$"):
                    limit_value = plan.params.get(limit_token[1:])
                else:
                    limit_value = limit_token
                try:
                    parsed_limit = int(limit_value)
                except (TypeError, ValueError):
                    parsed_limit = 0
                if parsed_limit < 1 or parsed_limit > max_result_rows:
                    violations.append("result_limit_exceeded")

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
