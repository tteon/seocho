"""Tiered Cypher compilation for versioned customer-query workloads."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Tuple

from .cypher_validator import FORBIDDEN_CYPHER_TOKENS
from .workloads import QueryFamilySpec, WITHDRAWAL_EXPLANATION


@dataclass(frozen=True, slots=True)
class WorkloadQueryPlan:
    """A parameterized, validated query selected before graph execution."""

    family_id: str
    tier: str
    cypher: str
    params: Mapping[str, Any]
    prompt_name: str
    prompt_version: str
    max_repair_attempts: int = 0


@dataclass(frozen=True, slots=True)
class Text2CypherFallbackPolicy:
    """Limits for unknown query families that require model generation."""

    allowed_labels: Tuple[str, ...]
    allowed_relationships: Tuple[str, ...]
    required_parameters: Tuple[str, ...] = ("workspace_id",)
    max_graph_hops: int = 4
    max_result_rows: int = 50
    max_repair_attempts: int = 1
    require_explain_before_execute: bool = True


_WITHDRAWAL_RECIPE = """
MATCH (w:Withdrawal {id: $withdrawal_id, workspace_id: $workspace_id})
OPTIONAL MATCH (w)-[:USES_ASSET]->(asset:Asset)
OPTIONAL MATCH (w)-[:USES_NETWORK]->(network:Network)
OPTIONAL MATCH (w)-[:HAS_STATUS]->(status:WithdrawalStatus)
OPTIONAL MATCH (w)-[:BLOCKED_BY]->(restriction:Restriction)
OPTIONAL MATCH (w)-[:SUBJECT_TO]->(policy:Policy)
OPTIONAL MATCH (policy)-[:SUPPORTED_BY]->(source:EvidenceSource)
RETURN w.id AS withdrawal_id,
       asset.id AS asset_id,
       network.id AS network_id,
       status.name AS withdrawal_state,
       restriction.code AS restriction_code,
       policy.id AS policy_id,
       source.id AS provenance_id
LIMIT $limit
""".strip()


def validate_workload_query(
    cypher: str,
    *,
    required_parameters: Tuple[str, ...],
    max_graph_hops: int,
) -> Tuple[str, ...]:
    """Return deterministic safety violations for a workload recipe."""

    normalized = " " + re.sub(r"\s+", " ", cypher.upper()) + " "
    violations: list[str] = []
    for token in FORBIDDEN_CYPHER_TOKENS:
        if token in normalized:
            violations.append(f"forbidden_token:{token.strip().lower().replace(' ', '_')}")
    if " RETURN " not in normalized:
        violations.append("missing_return_clause")
    if " LIMIT $LIMIT " not in normalized:
        violations.append("missing_parameterized_limit")
    for parameter in required_parameters:
        if f"${parameter}" not in cypher:
            violations.append(f"missing_parameter:{parameter}")
    for lower, upper in re.findall(r"\*(\d+)\.\.(\d+)", cypher):
        if int(upper) > max_graph_hops:
            violations.append("graph_hop_limit_exceeded")
    return tuple(violations)


def compile_workload_query(
    family: QueryFamilySpec,
    *,
    workspace_id: str,
    input_slots: Mapping[str, Any],
    limit: int = 50,
) -> WorkloadQueryPlan:
    """Compile a known workload to an approved recipe without LLM Cypher."""

    if not workspace_id.strip():
        raise ValueError("workspace_id is required")
    if family.intent_id != WITHDRAWAL_EXPLANATION.intent_id:
        raise KeyError(f"no approved Cypher recipe for {family.intent_id}")
    withdrawal_id = str(input_slots.get("withdrawal_id", "")).strip()
    if not withdrawal_id:
        raise ValueError("withdrawal_id is required")

    bounded_limit = max(1, min(int(limit), 50))
    violations = validate_workload_query(
        _WITHDRAWAL_RECIPE,
        required_parameters=("workspace_id", "withdrawal_id", "limit"),
        max_graph_hops=family.safety.max_graph_hops,
    )
    if violations:
        raise ValueError("unsafe workload recipe: " + ", ".join(violations))
    return WorkloadQueryPlan(
        family_id=family.intent_id,
        tier="approved_recipe",
        cypher=_WITHDRAWAL_RECIPE,
        params={
            "workspace_id": workspace_id,
            "withdrawal_id": withdrawal_id,
            "limit": bounded_limit,
        },
        prompt_name=family.prompt.name,
        prompt_version=family.prompt.version,
    )


def fallback_policy_for(family: QueryFamilySpec) -> Text2CypherFallbackPolicy:
    """Build the constrained policy used only when no recipe can answer."""

    return Text2CypherFallbackPolicy(
        allowed_labels=family.required_entity_types,
        allowed_relationships=family.required_relations,
        max_graph_hops=family.safety.max_graph_hops,
    )

