from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .ontology import Ontology

_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")


def _stable_json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def parse_semver(value: str) -> Optional[Tuple[int, int, int]]:
    """Parse the numeric core of a semantic version string.

    Build metadata and prerelease suffixes are accepted for validity but ignored
    for ordering. SEOCHO treats ontology version ordering as a governance aid,
    not as a package manager resolver.
    """

    text = str(value or "").strip()
    match = _SEMVER_RE.match(text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def is_valid_semver(value: str) -> bool:
    return parse_semver(value) is not None


def ontology_schema_fingerprint(ontology: Ontology) -> str:
    """Return a stable hash for schema content, excluding version metadata."""

    payload = dict(ontology.to_dict())
    payload.pop("version", None)
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class OntologyVersionIdentity:
    package_id: str
    ontology_name: str
    ontology_version: str
    graph_model: str
    schema_fingerprint: str
    version_valid: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OntologyUpgradePlan:
    package_id: str
    from_version: str
    to_version: str
    from_schema_fingerprint: str
    to_schema_fingerprint: str
    recommended_bump: str
    version_valid: bool
    version_satisfies_recommendation: bool
    requires_migration: bool
    reindex_required: bool
    shacl_revalidation_required: bool
    query_cache_invalidation: bool
    prompt_context_invalidation: bool
    indexing_effects: List[str] = field(default_factory=list)
    query_effects: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    changes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def ontology_version_identity(ontology: Ontology) -> OntologyVersionIdentity:
    return OntologyVersionIdentity(
        package_id=ontology.package_id,
        ontology_name=ontology.name,
        ontology_version=ontology.version,
        graph_model=ontology.graph_model,
        schema_fingerprint=ontology_schema_fingerprint(ontology),
        version_valid=is_valid_semver(ontology.version),
    )


def build_ontology_upgrade_plan(left: Ontology, right: Ontology) -> OntologyUpgradePlan:
    """Explain how an ontology revision affects indexing and querying.

    The plan bridges author-time ontology diffing with runtime behavior:
    indexing can decide whether to revalidate or reindex, while query layers can
    invalidate prompt/context caches and route away from stale graph contexts.
    """

    from .ontology_governance import diff_ontologies

    diff = diff_ontologies(left, right)
    changes = diff.changes
    left_fp = ontology_schema_fingerprint(left)
    right_fp = ontology_schema_fingerprint(right)
    schema_changed = left_fp != right_fp
    version_valid = is_valid_semver(left.version) and is_valid_semver(right.version)
    version_satisfies = _version_satisfies_recommendation(
        left.version,
        right.version,
        diff.recommended_bump,
    )

    indexing_effects: List[str] = []
    query_effects: List[str] = []
    warnings = list(diff.migration_warnings)

    node_changes = changes.get("nodes", {})
    rel_changes = changes.get("relationships", {})
    metadata_changes = changes.get("metadata", {}).get("changed", [])

    if node_changes.get("added") or rel_changes.get("added"):
        indexing_effects.append("New node or relationship types should be included in extraction prompts and SHACL validation.")
        query_effects.append("Query planners can use the new labels or relationship types after graph reindexing or refresh.")
    if node_changes.get("removed") or rel_changes.get("removed"):
        indexing_effects.append("Removed node or relationship types require migration or cleanup of indexed graph data.")
        query_effects.append("Queries that reference removed labels or relationship types must be blocked or rewritten.")
    if node_changes.get("changed") or rel_changes.get("changed"):
        indexing_effects.append("Changed definitions or constraints require SHACL revalidation of candidate graph writes.")
        query_effects.append("Prompt context, deterministic query plans, and Cypher validation slices must be refreshed.")
    if "graph_model" in metadata_changes:
        indexing_effects.append("Graph model changes require an indexing target review before writing new data.")
        query_effects.append("Query strategy and backend routing must be reviewed for the new graph model.")
    if "namespace" in metadata_changes or "package_id" in metadata_changes:
        query_effects.append("External identifiers changed; cached semantic artifacts and answer provenance should be invalidated.")

    if not version_valid:
        warnings.append("Ontology versions should use semantic versioning: MAJOR.MINOR.PATCH.")
    elif not version_satisfies:
        warnings.append(
            f"Ontology version change {left.version!r} -> {right.version!r} does not satisfy the recommended {diff.recommended_bump} bump."
        )

    shacl_revalidation_required = schema_changed and (
        bool(node_changes.get("changed"))
        or bool(rel_changes.get("changed"))
        or bool(node_changes.get("added"))
        or bool(rel_changes.get("added"))
        or diff.requires_migration
    )

    return OntologyUpgradePlan(
        package_id=diff.package_id,
        from_version=left.version,
        to_version=right.version,
        from_schema_fingerprint=left_fp,
        to_schema_fingerprint=right_fp,
        recommended_bump=diff.recommended_bump,
        version_valid=version_valid,
        version_satisfies_recommendation=version_satisfies,
        requires_migration=diff.requires_migration,
        reindex_required=schema_changed,
        shacl_revalidation_required=shacl_revalidation_required,
        query_cache_invalidation=schema_changed or left.version != right.version,
        prompt_context_invalidation=schema_changed or left.version != right.version,
        indexing_effects=indexing_effects,
        query_effects=query_effects,
        warnings=warnings,
        changes=changes,
    )


def _version_satisfies_recommendation(
    left_version: str,
    right_version: str,
    recommended_bump: str,
) -> bool:
    if recommended_bump == "none":
        return True
    left = parse_semver(left_version)
    right = parse_semver(right_version)
    if left is None or right is None:
        return False
    if recommended_bump == "patch":
        return right > left
    if recommended_bump == "minor":
        return right[0] == left[0] and right[1] > left[1]
    if recommended_bump == "major":
        return right[0] > left[0]
    return True
