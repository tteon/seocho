from __future__ import annotations

import os
from collections import Counter
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

from rule_constraints import RuleSet, apply_rules_to_graph, infer_rules_from_graph
from rule_export import export_ruleset_to_cypher, export_ruleset_to_shacl
from rule_profile_store import get_rule_profile, list_rule_profiles, save_rule_profile


class RuleInferRequest(BaseModel):
    workspace_id: str = Field(default="default", pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$")
    graph: Dict[str, Any]
    required_threshold: float = Field(default=0.98, ge=0.0, le=1.0)
    enum_max_size: int = Field(default=20, ge=1, le=200)


class RuleInferResponse(BaseModel):
    workspace_id: str
    rule_profile: Dict[str, Any]
    shacl_like: Dict[str, Any]


class RuleValidateRequest(BaseModel):
    workspace_id: str = Field(default="default", pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$")
    graph: Dict[str, Any]
    rule_profile: Optional[Dict[str, Any]] = None
    required_threshold: float = Field(default=0.98, ge=0.0, le=1.0)
    enum_max_size: int = Field(default=20, ge=1, le=200)


class RuleValidateResponse(BaseModel):
    workspace_id: str
    graph: Dict[str, Any]
    rule_profile: Dict[str, Any]
    validation_summary: Dict[str, Any]


class RuleProfileCreateRequest(BaseModel):
    workspace_id: str = Field(default="default", pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$")
    name: Optional[str] = None
    rule_profile: Dict[str, Any]


class RuleProfileCreateResponse(BaseModel):
    workspace_id: str
    profile_id: str
    name: str
    created_at: str
    schema_version: str
    rule_count: int


class RuleProfileGetResponse(BaseModel):
    workspace_id: str
    profile_id: str
    name: str
    created_at: str
    schema_version: str
    rule_count: int
    rule_profile: Dict[str, Any]


class RuleProfileListResponse(BaseModel):
    workspace_id: str
    profiles: list[Dict[str, Any]]


class RuleExportCypherRequest(BaseModel):
    workspace_id: str = Field(default="default", pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$")
    profile_id: Optional[str] = None
    rule_profile: Optional[Dict[str, Any]] = None


class RuleExportCypherResponse(BaseModel):
    workspace_id: str
    schema_version: str
    statements: list[str]
    unsupported_rules: list[Dict[str, Any]]


class RuleExportShaclRequest(BaseModel):
    workspace_id: str = Field(default="default", pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$")
    profile_id: Optional[str] = None
    rule_profile: Optional[Dict[str, Any]] = None


class RuleExportShaclResponse(BaseModel):
    workspace_id: str
    schema_version: str
    shapes: list[Dict[str, Any]]
    turtle: str
    unsupported_rules: list[Dict[str, Any]]


class RuleAssessRequest(BaseModel):
    workspace_id: str = Field(default="default", pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$")
    graph: Dict[str, Any]
    rule_profile: Optional[Dict[str, Any]] = None
    required_threshold: float = Field(default=0.98, ge=0.0, le=1.0)
    enum_max_size: int = Field(default=20, ge=1, le=200)


class RuleAssessResponse(BaseModel):
    workspace_id: str
    rule_profile: Dict[str, Any]
    shacl_like: Dict[str, Any]
    validation_summary: Dict[str, Any]
    violation_breakdown: list[Dict[str, Any]]
    export_preview: Dict[str, Any]
    practical_readiness: Dict[str, Any]


def _rule_profile_dir() -> str:
    return os.getenv("RULE_PROFILE_DIR", "outputs/rule_profiles")


def infer_rule_profile(request: RuleInferRequest) -> RuleInferResponse:
    ruleset = infer_rules_from_graph(
        extracted_data=request.graph,
        required_threshold=request.required_threshold,
        enum_max_size=request.enum_max_size,
    )
    return RuleInferResponse(
        workspace_id=request.workspace_id,
        rule_profile=ruleset.to_dict(),
        shacl_like=ruleset.to_shacl_like(),
    )


def validate_rule_profile(request: RuleValidateRequest) -> RuleValidateResponse:
    if request.rule_profile:
        ruleset = RuleSet.from_dict(request.rule_profile)
    else:
        ruleset = infer_rules_from_graph(
            extracted_data=request.graph,
            required_threshold=request.required_threshold,
            enum_max_size=request.enum_max_size,
        )

    validated = apply_rules_to_graph(request.graph, ruleset)
    return RuleValidateResponse(
        workspace_id=request.workspace_id,
        graph=validated,
        rule_profile=ruleset.to_dict(),
        validation_summary=validated.get("rule_validation_summary", {}),
    )


def create_rule_profile(request: RuleProfileCreateRequest) -> RuleProfileCreateResponse:
    saved = save_rule_profile(
        workspace_id=request.workspace_id,
        name=request.name,
        rule_profile=request.rule_profile,
        base_dir=_rule_profile_dir(),
    )
    return RuleProfileCreateResponse(
        workspace_id=saved["workspace_id"],
        profile_id=saved["profile_id"],
        name=saved["name"],
        created_at=saved["created_at"],
        schema_version=saved["schema_version"],
        rule_count=saved["rule_count"],
    )


def read_rule_profile(workspace_id: str, profile_id: str) -> RuleProfileGetResponse:
    payload = get_rule_profile(
        workspace_id=workspace_id,
        profile_id=profile_id,
        base_dir=_rule_profile_dir(),
    )
    return RuleProfileGetResponse(**payload)


def read_rule_profiles(workspace_id: str) -> RuleProfileListResponse:
    profiles = list_rule_profiles(workspace_id=workspace_id, base_dir=_rule_profile_dir())
    return RuleProfileListResponse(workspace_id=workspace_id, profiles=profiles)


def export_rule_profile_to_cypher(request: RuleExportCypherRequest) -> RuleExportCypherResponse:
    if request.rule_profile:
        profile = request.rule_profile
    elif request.profile_id:
        payload = get_rule_profile(
            workspace_id=request.workspace_id,
            profile_id=request.profile_id,
            base_dir=_rule_profile_dir(),
        )
        profile = payload["rule_profile"]
    else:
        raise ValueError("either profile_id or rule_profile must be provided")

    exported = export_ruleset_to_cypher(profile)
    return RuleExportCypherResponse(
        workspace_id=request.workspace_id,
        schema_version=exported["schema_version"],
        statements=exported["statements"],
        unsupported_rules=exported["unsupported_rules"],
    )


def export_rule_profile_to_shacl(request: RuleExportShaclRequest) -> RuleExportShaclResponse:
    if request.rule_profile:
        profile = request.rule_profile
    elif request.profile_id:
        payload = get_rule_profile(
            workspace_id=request.workspace_id,
            profile_id=request.profile_id,
            base_dir=_rule_profile_dir(),
        )
        profile = payload["rule_profile"]
    else:
        raise ValueError("either profile_id or rule_profile must be provided")

    exported = export_ruleset_to_shacl(profile)
    return RuleExportShaclResponse(
        workspace_id=request.workspace_id,
        schema_version=exported["schema_version"],
        shapes=exported["shapes"],
        turtle=exported["turtle"],
        unsupported_rules=exported["unsupported_rules"],
    )


def assess_rule_profile(request: RuleAssessRequest) -> RuleAssessResponse:
    if request.rule_profile:
        ruleset = RuleSet.from_dict(request.rule_profile)
    else:
        ruleset = infer_rules_from_graph(
            extracted_data=request.graph,
            required_threshold=request.required_threshold,
            enum_max_size=request.enum_max_size,
        )

    validated_graph = apply_rules_to_graph(request.graph, ruleset)
    validation_summary = validated_graph.get("rule_validation_summary", {})
    violation_breakdown = _collect_violation_breakdown(validated_graph)

    exported = export_ruleset_to_cypher(ruleset.to_dict())
    practical_readiness = _compute_practical_readiness(
        validation_summary=validation_summary,
        total_rules=len(ruleset.rules),
        unsupported_count=len(exported.get("unsupported_rules", [])),
    )
    practical_readiness["top_violations"] = violation_breakdown[:5]

    return RuleAssessResponse(
        workspace_id=request.workspace_id,
        rule_profile=ruleset.to_dict(),
        shacl_like=ruleset.to_shacl_like(),
        validation_summary=validation_summary,
        violation_breakdown=violation_breakdown,
        export_preview=exported,
        practical_readiness=practical_readiness,
    )


def _collect_violation_breakdown(validated_graph: Dict[str, Any]) -> list[Dict[str, Any]]:
    counter: Counter[tuple[str, str]] = Counter()
    for node in validated_graph.get("nodes", []):
        for violation in node.get("rule_validation", {}).get("violations", []):
            key = (str(violation.get("rule", "unknown")), str(violation.get("property", "unknown")))
            counter[key] += 1

    items = []
    for (rule_kind, prop), count in counter.most_common():
        items.append({"rule": rule_kind, "property": prop, "count": count})
    return items


def _compute_practical_readiness(
    validation_summary: Dict[str, Any],
    total_rules: int,
    unsupported_count: int,
) -> Dict[str, Any]:
    total_nodes = int(validation_summary.get("total_nodes", 0))
    failed_nodes = int(validation_summary.get("failed_nodes", 0))
    passed_nodes = max(total_nodes - failed_nodes, 0)

    pass_ratio = 1.0 if total_nodes == 0 else passed_nodes / total_nodes
    enforceable_rules = max(total_rules - unsupported_count, 0)
    enforceable_ratio = 1.0 if total_rules == 0 else enforceable_rules / total_rules

    score = round((pass_ratio * 0.65) + (enforceable_ratio * 0.35), 3)
    status: Literal["ready", "caution", "blocked"]
    if pass_ratio >= 0.95 and enforceable_ratio >= 0.5:
        status = "ready"
    elif pass_ratio >= 0.8:
        status = "caution"
    else:
        status = "blocked"

    recommendations: list[str] = []
    if failed_nodes > 0:
        recommendations.append(
            "Fix failing nodes first: use violation_breakdown to target top offending properties."
        )
    if unsupported_count > 0:
        recommendations.append(
            "Some rules cannot be translated to DB constraints; enforce them in app-level validation."
        )
    if status == "ready":
        recommendations.append(
            "You can apply exported Cypher constraints and keep /rules/validate in ingestion CI."
        )

    return {
        "status": status,
        "score": score,
        "pass_ratio": round(pass_ratio, 3),
        "enforceable_ratio": round(enforceable_ratio, 3),
        "failed_nodes": failed_nodes,
        "total_nodes": total_nodes,
        "total_rules": total_rules,
        "unsupported_rules": unsupported_count,
        "recommendations": recommendations,
    }
