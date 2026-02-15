from __future__ import annotations

import os
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from rule_constraints import RuleSet, apply_rules_to_graph, infer_rules_from_graph
from rule_export import export_ruleset_to_cypher
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
