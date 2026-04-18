from __future__ import annotations

import os
from collections import Counter
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

from rule_constraints import RuleSet, apply_rules_to_graph, infer_rules_from_graph
from rule_export import export_ruleset_to_cypher, export_ruleset_to_shacl
from rule_profile_store import get_rule_profile, list_rule_profiles, save_rule_profile


class ReadinessBlockedError(Exception):
    """Raised when a rule profile cannot be promoted because readiness is blocked.

    Carries the full practical_readiness verdict so the caller can surface
    actionable diagnostics (top violations, failed node counts, recommendations).
    """

    def __init__(self, verdict: Dict[str, Any]):
        super().__init__(
            f"readiness_blocked status={verdict.get('status')} "
            f"pass_ratio={verdict.get('pass_ratio')}"
        )
        self.verdict = verdict


class RuleInferRequest(BaseModel):
    """Infer SHACL-like constraints from an extracted graph."""

    workspace_id: str = Field(default="default", pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$", description="Workspace scope.")
    graph: Dict[str, Any] = Field(description="Extracted graph data with 'nodes' and 'relationships' arrays.")
    required_threshold: float = Field(default=0.98, ge=0.0, le=1.0, description="Minimum property completeness ratio to infer a 'required' rule.")
    enum_max_size: int = Field(default=20, ge=1, le=200, description="Maximum distinct values to infer an 'enum' constraint.")


class RuleInferResponse(BaseModel):
    """Inferred rule profile and SHACL-like shape document."""

    workspace_id: str = Field(description="Workspace scope.")
    rule_profile: Dict[str, Any] = Field(description="Inferred rules in internal format (schema_version + rules array).")
    shacl_like: Dict[str, Any] = Field(description="SHACL-inspired shape document for human review.")


class RuleValidateRequest(BaseModel):
    """Validate an extracted graph against a rule profile."""

    workspace_id: str = Field(default="default", pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$", description="Workspace scope.")
    graph: Dict[str, Any] = Field(description="Extracted graph data to validate.")
    rule_profile: Optional[Dict[str, Any]] = Field(default=None, description="Rule profile to validate against; inferred from graph if omitted.")
    required_threshold: float = Field(default=0.98, ge=0.0, le=1.0, description="Completeness threshold for 'required' rule inference.")
    enum_max_size: int = Field(default=20, ge=1, le=200, description="Max distinct values for 'enum' rule inference.")


class RuleValidateResponse(BaseModel):
    """Validation results with annotated graph and summary."""

    workspace_id: str = Field(description="Workspace scope.")
    graph: Dict[str, Any] = Field(description="Graph with per-node rule_validation annotations.")
    rule_profile: Dict[str, Any] = Field(description="Rule profile used for validation.")
    validation_summary: Dict[str, Any] = Field(description="Aggregate counts: total_nodes, passed_nodes, failed_nodes.")


class RuleProfileCreateRequest(BaseModel):
    """Persist a rule profile for reuse."""

    workspace_id: str = Field(default="default", pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$", description="Workspace scope.")
    name: Optional[str] = Field(default=None, description="Human-readable profile name; auto-generated if omitted.")
    rule_profile: Dict[str, Any] = Field(description="Rule profile payload to persist.")
    validation_graph: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Optional graph to assess the rule profile against before persistence. "
            "When supplied, practical readiness is computed and the request is rejected "
            "with HTTP 409 if status is 'blocked'. Omit to skip the gate."
        ),
    )
    acknowledge_blocked_readiness: bool = Field(
        default=False,
        description=(
            "Explicit override that allows persistence even when validation_graph "
            "produces a 'blocked' verdict. Requires validation_graph to be meaningful. "
            "The verdict is still returned on the response so the override is auditable."
        ),
    )


class RuleProfileCreateResponse(BaseModel):
    """Confirmation of a newly created rule profile."""

    workspace_id: str = Field(description="Workspace scope.")
    profile_id: str = Field(description="Unique identifier for the saved profile.")
    name: str = Field(description="Profile display name.")
    created_at: str = Field(description="ISO-8601 creation timestamp.")
    schema_version: str = Field(description="Rule schema version (e.g. 'rules.v1').")
    rule_count: int = Field(description="Number of rules in the profile.")
    readiness_verdict: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Readiness verdict when validation_graph was supplied; null when the gate was skipped.",
    )


class RuleProfileGetResponse(BaseModel):
    """Full rule profile with metadata."""

    workspace_id: str = Field(description="Workspace scope.")
    profile_id: str = Field(description="Profile identifier.")
    name: str = Field(description="Profile display name.")
    created_at: str = Field(description="ISO-8601 creation timestamp.")
    schema_version: str = Field(description="Rule schema version.")
    rule_count: int = Field(description="Number of rules.")
    rule_profile: Dict[str, Any] = Field(description="Full rule profile payload.")


class RuleProfileListResponse(BaseModel):
    """List of available rule profiles."""

    workspace_id: str = Field(description="Workspace scope.")
    profiles: list[Dict[str, Any]] = Field(description="Summary metadata for each profile.")


class RuleExportCypherRequest(BaseModel):
    """Export a rule profile as Neo4j/DozerDB Cypher constraint statements."""

    workspace_id: str = Field(default="default", pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$", description="Workspace scope.")
    profile_id: Optional[str] = Field(default=None, description="Profile ID to export; required if rule_profile is omitted.")
    rule_profile: Optional[Dict[str, Any]] = Field(default=None, description="Inline rule profile; used if profile_id is omitted.")


class RuleExportCypherResponse(BaseModel):
    """Cypher constraint export result."""

    workspace_id: str = Field(description="Workspace scope.")
    schema_version: str = Field(description="Rule schema version.")
    statements: list[str] = Field(description="Executable Cypher constraint statements.")
    unsupported_rules: list[Dict[str, Any]] = Field(description="Rules that cannot be expressed as Cypher constraints.")


class RuleExportShaclRequest(BaseModel):
    """Export a rule profile as SHACL shapes (JSON + Turtle)."""

    workspace_id: str = Field(default="default", pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$", description="Workspace scope.")
    profile_id: Optional[str] = Field(default=None, description="Profile ID to export.")
    rule_profile: Optional[Dict[str, Any]] = Field(default=None, description="Inline rule profile.")


class RuleExportShaclResponse(BaseModel):
    """SHACL shape export result."""

    workspace_id: str = Field(description="Workspace scope.")
    schema_version: str = Field(description="Rule schema version.")
    shapes: list[Dict[str, Any]] = Field(description="SHACL-like shape documents (JSON).")
    turtle: str = Field(description="SHACL shapes serialized as Turtle RDF.")
    unsupported_rules: list[Dict[str, Any]] = Field(description="Rules that cannot be expressed as SHACL shapes.")


class RuleAssessRequest(BaseModel):
    """Full assessment: infer + validate + export preview."""

    workspace_id: str = Field(default="default", pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$", description="Workspace scope.")
    graph: Dict[str, Any] = Field(description="Extracted graph data to assess.")
    rule_profile: Optional[Dict[str, Any]] = Field(default=None, description="Rule profile to assess against; inferred if omitted.")
    required_threshold: float = Field(default=0.98, ge=0.0, le=1.0, description="Completeness threshold for 'required' rules.")
    enum_max_size: int = Field(default=20, ge=1, le=200, description="Max distinct values for 'enum' rules.")


class RuleAssessResponse(BaseModel):
    """Comprehensive rule assessment with export readiness."""

    workspace_id: str = Field(description="Workspace scope.")
    rule_profile: Dict[str, Any] = Field(description="Rule profile used for assessment.")
    shacl_like: Dict[str, Any] = Field(description="SHACL-like shape document.")
    validation_summary: Dict[str, Any] = Field(description="Aggregate validation counts.")
    violation_breakdown: list[Dict[str, Any]] = Field(description="Per-rule violation details.")
    export_preview: Dict[str, Any] = Field(description="Preview of Cypher and SHACL exports.")
    practical_readiness: Dict[str, Any] = Field(description="Readiness verdict for production promotion.")


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
    verdict = _enforce_readiness_gate(
        rule_profile=request.rule_profile,
        validation_graph=request.validation_graph,
        acknowledge=request.acknowledge_blocked_readiness,
    )
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
        readiness_verdict=verdict,
    )


def _enforce_readiness_gate(
    *,
    rule_profile: Dict[str, Any],
    validation_graph: Optional[Dict[str, Any]],
    acknowledge: bool,
) -> Optional[Dict[str, Any]]:
    """Return the readiness verdict, or raise ReadinessBlockedError on blocked.

    Returns None when validation_graph is omitted (gate disengaged, legacy behavior).
    When a graph is supplied, computes practical readiness exactly the same way
    /rules/assess does. A 'blocked' verdict raises unless ``acknowledge`` is True,
    in which case the verdict is returned for auditing but persistence proceeds.
    """
    if validation_graph is None:
        return None

    ruleset = RuleSet.from_dict(rule_profile)
    validated_graph = apply_rules_to_graph(validation_graph, ruleset)
    summary = validated_graph.get("rule_validation_summary", {})
    exported = export_ruleset_to_cypher(ruleset.to_dict())
    verdict = _compute_practical_readiness(
        validation_summary=summary,
        total_rules=len(ruleset.rules),
        unsupported_count=len(exported.get("unsupported_rules", [])),
    )
    verdict["top_violations"] = _collect_violation_breakdown(validated_graph)[:5]
    if verdict["status"] == "blocked" and not acknowledge:
        raise ReadinessBlockedError(verdict=verdict)
    return verdict


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
