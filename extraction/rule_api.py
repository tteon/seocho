from __future__ import annotations

import logging
import os
from collections import Counter
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

from rule_constraints import RuleSet, apply_rules_to_graph, infer_rules_from_graph
from rule_export import export_ruleset_to_cypher, export_ruleset_to_shacl
from rule_profile_store import get_rule_profile, list_rule_profiles, save_rule_profile

logger = logging.getLogger(__name__)


def _active_ontology_hash(workspace_id: str) -> str:
    """Return the active ontology context hash for a workspace.

    Phase 6 promotion-gate hook. Reads
    ``RuntimeOntologyRegistry.active_context_hashes(workspace_id)`` and
    returns the single unique hash if the workspace has exactly one
    ontology registered. Returns empty string when:

    - the registry holds no ontology for the workspace (Phase 1.5 not
      activated);
    - the workspace has multiple distinct ontologies registered (multi-
      graph workspace; the rules API does not carry a graph_id, so we
      cannot pick safely — Phase 5's "unknown" trichotomy applies);
    - the registry import or lookup fails (defensive — drift detection
      stays inert rather than blocking the legacy promotion path).

    Empty string preserves the legacy behavior: ``infer_rules_from_graph``
    and ``save_rule_profile`` accept an empty hash and produce
    ``ontology_identity_hash=""`` artifacts that read as ``status=unknown``.
    """

    try:
        from runtime.ontology_registry import get_runtime_ontology_registry
    except Exception:  # pragma: no cover — defensive
        logger.debug("Runtime ontology registry import failed in rule_api.", exc_info=True)
        return ""

    try:
        registry = get_runtime_ontology_registry()
        hashes = registry.active_context_hashes(workspace_id=workspace_id)
    except Exception:
        logger.debug(
            "Active ontology hash lookup failed for workspace=%s",
            workspace_id,
            exc_info=True,
        )
        return ""

    unique = {value for value in hashes.values() if value}
    if len(unique) == 1:
        return next(iter(unique))
    return ""


def _assess_rule_profile_against_active(
    profile_payload: Dict[str, Any],
    *,
    active_hash: str,
) -> Optional[Dict[str, Any]]:
    """Compare a rule profile dict's stored hash against the active workspace hash.

    Returns the artifact_ontology_mismatch trichotomy block (match / drift /
    unknown) or None when there's nothing to compare (no active hash).
    """

    if not active_hash:
        return None
    stored = str(profile_payload.get("ontology_identity_hash", "") or "").strip()
    if not stored:
        return {
            "stored_ontology_hash": "",
            "active_ontology_hash": active_hash,
            "mismatch": False,
            "status": "unknown",
            "warning": (
                "Rule profile has no ontology_identity_hash stamped. "
                "Re-infer or re-save under Phase 6 to gain hash parity."
            ),
        }
    if stored == active_hash:
        return {
            "stored_ontology_hash": stored,
            "active_ontology_hash": active_hash,
            "mismatch": False,
            "status": "match",
            "warning": "",
        }
    return {
        "stored_ontology_hash": stored,
        "active_ontology_hash": active_hash,
        "mismatch": True,
        "status": "drift",
        "warning": (
            "Rule profile ontology_identity_hash differs from active runtime hash. "
            "Refuse application or re-derive the profile from the active ontology."
        ),
    }


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
    ontology_identity_hash: str = Field(default="", description="Active ontology context hash stamped on the inferred profile (Phase 6). Empty when no unique active ontology is registered for the workspace.")


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


class RuleProfileCreateResponse(BaseModel):
    """Confirmation of a newly created rule profile."""

    workspace_id: str = Field(description="Workspace scope.")
    profile_id: str = Field(description="Unique identifier for the saved profile.")
    name: str = Field(description="Profile display name.")
    created_at: str = Field(description="ISO-8601 creation timestamp.")
    schema_version: str = Field(description="Rule schema version (e.g. 'rules.v1').")
    rule_count: int = Field(description="Number of rules in the profile.")
    ontology_identity_hash: str = Field(default="", description="Active ontology context hash stamped on the saved profile (Phase 6). Empty when no unique active ontology is registered for the workspace.")


class RuleProfileGetResponse(BaseModel):
    """Full rule profile with metadata."""

    workspace_id: str = Field(description="Workspace scope.")
    profile_id: str = Field(description="Profile identifier.")
    name: str = Field(description="Profile display name.")
    created_at: str = Field(description="ISO-8601 creation timestamp.")
    schema_version: str = Field(description="Rule schema version.")
    rule_count: int = Field(description="Number of rules.")
    rule_profile: Dict[str, Any] = Field(description="Full rule profile payload.")
    ontology_identity_hash: str = Field(default="", description="Stored ontology context hash (Phase 5).")
    artifact_ontology_mismatch: Optional[Dict[str, Any]] = Field(default=None, description="Drift assessment vs the workspace's active ontology hash (Phase 6); status in {match, drift, unknown}.")


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
    ontology_identity_hash: str = Field(default="", description="Ontology context hash stamped on the rule profile (Phase 6).")
    artifact_ontology_mismatch: Optional[Dict[str, Any]] = Field(default=None, description="Drift assessment vs the workspace's active ontology hash (Phase 6); status in {match, drift, unknown}.")


def _rule_profile_dir() -> str:
    return os.getenv("RULE_PROFILE_DIR", "outputs/rule_profiles")


def infer_rule_profile(request: RuleInferRequest) -> RuleInferResponse:
    active_hash = _active_ontology_hash(request.workspace_id)
    ruleset = infer_rules_from_graph(
        extracted_data=request.graph,
        required_threshold=request.required_threshold,
        enum_max_size=request.enum_max_size,
        ontology_identity_hash=active_hash,
    )
    return RuleInferResponse(
        workspace_id=request.workspace_id,
        rule_profile=ruleset.to_dict(),
        shacl_like=ruleset.to_shacl_like(),
        ontology_identity_hash=ruleset.ontology_identity_hash,
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
    # Phase 6: caller-provided rule_profile may already carry a hash
    # (e.g. from /rules/infer); save_rule_profile honors that. Otherwise
    # we stamp the active workspace hash so saved profiles always carry
    # parity metadata when the registry has data.
    active_hash = _active_ontology_hash(request.workspace_id)
    saved = save_rule_profile(
        workspace_id=request.workspace_id,
        name=request.name,
        rule_profile=request.rule_profile,
        base_dir=_rule_profile_dir(),
        ontology_identity_hash=active_hash,
    )
    return RuleProfileCreateResponse(
        workspace_id=saved["workspace_id"],
        profile_id=saved["profile_id"],
        name=saved["name"],
        created_at=saved["created_at"],
        schema_version=saved["schema_version"],
        rule_count=saved["rule_count"],
        ontology_identity_hash=saved.get("ontology_identity_hash", ""),
    )


def read_rule_profile(workspace_id: str, profile_id: str) -> RuleProfileGetResponse:
    # Phase 6: surface drift detection automatically by comparing the
    # stored hash against the workspace's active ontology hash. Empty
    # active hash → no drift block (matches Phase 5's reads-don't-fail
    # contract).
    active_hash = _active_ontology_hash(workspace_id)
    payload = get_rule_profile(
        workspace_id=workspace_id,
        profile_id=profile_id,
        base_dir=_rule_profile_dir(),
        expected_ontology_hash=active_hash,
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
    active_hash = _active_ontology_hash(request.workspace_id)

    if request.rule_profile:
        ruleset = RuleSet.from_dict(request.rule_profile)
        # Phase 6: assessing an externally-supplied profile compares its
        # stored hash to the active workspace hash. Drift surfaces in
        # the response without blocking the assessment itself —
        # /rules/assess remains an inspection endpoint; promotion gates
        # consume the trichotomy verdict.
        mismatch = _assess_rule_profile_against_active(
            ruleset.to_dict(),
            active_hash=active_hash,
        )
    else:
        # Inferring fresh: stamp the active hash so the returned
        # profile carries parity metadata that downstream callers can
        # save via /rules/profiles without an extra registry lookup.
        ruleset = infer_rules_from_graph(
            extracted_data=request.graph,
            required_threshold=request.required_threshold,
            enum_max_size=request.enum_max_size,
            ontology_identity_hash=active_hash,
        )
        mismatch = _assess_rule_profile_against_active(
            ruleset.to_dict(),
            active_hash=active_hash,
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
        ontology_identity_hash=ruleset.ontology_identity_hash,
        artifact_ontology_mismatch=mismatch,
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
