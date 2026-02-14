from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from rule_constraints import RuleSet, apply_rules_to_graph, infer_rules_from_graph


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
