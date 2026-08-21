"""Versioned RDF artifacts derived from one SEOCHO JSON-LD ontology source."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Mapping

from .governance import _render_shacl_turtle

if TYPE_CHECKING:
    from .core import Ontology


@dataclass(frozen=True)
class RdfOntologyBundle:
    """Portable, content-addressed ontology artifacts for RDF consumers."""

    directory: Path
    jsonld_path: Path
    turtle_path: Path
    shacl_path: Path
    manifest_path: Path
    agent_profiles_dir: Path
    digest: str


def build_rdf_ontology_bundle(
    ontology: "Ontology",
    output_dir: str | Path,
    *,
    module_specs: Mapping[str, Mapping[str, Any]] | None = None,
) -> RdfOntologyBundle:
    """Write JSON-LD source plus derived Turtle and SHACL Turtle artifacts.

    JSON-LD remains SEOCHO's authored representation. Turtle is deliberately a
    derived interchange format so Oxigraph and Neo4j/n10s consume exactly the
    same vocabulary without creating a second editable schema.
    """
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    jsonld_path = directory / "ontology.jsonld"
    turtle_path = directory / "ontology.ttl"
    shacl_path = directory / "shapes.ttl"
    manifest_path = directory / "manifest.json"
    agent_profiles_dir = directory / "agent-profiles"

    jsonld = ontology.to_jsonld()
    jsonld_path.write_text(
        json.dumps(jsonld, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    # The established serializer uses rdflib and therefore raises an actionable
    # install error when RDF support is not installed.
    ontology.to_ttl(turtle_path)
    shacl_path.write_text(_render_shacl_turtle(ontology.to_shacl()), encoding="utf-8")

    files: Dict[str, str] = {}
    for path in (jsonld_path, turtle_path, shacl_path):
        files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "seocho.rdf_ontology_bundle.v1",
                "ontology": {
                    "name": ontology.name,
                    "package_id": ontology.package_id,
                    "version": ontology.version,
                    "namespace": ontology.namespace,
                },
                "files": files,
                "bundle_sha256": digest,
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    # Agent-facing payloads are derived, compact views. They are deliberately
    # outside the RDF governance manifest: changing a prompt budget or role
    # must not change the canonical ontology or invalidate its SHACL receipt.
    agent_profiles_dir.mkdir(exist_ok=True)
    from .context import compile_ontology_context

    compiled = compile_ontology_context(ontology)
    profiles: Dict[str, Dict[str, Any]] = {
        "indexing": {
            "allowed_node_labels": compiled.descriptor.node_labels,
            "allowed_relationship_types": compiled.descriptor.relationship_types,
            "extraction_context": compiled.extraction_context,
        },
        "query": {
            "allowed_node_labels": compiled.descriptor.node_labels,
            "allowed_relationship_types": compiled.descriptor.relationship_types,
            "query_context": compiled.query_context,
            "query_profile": compiled.query_profile,
        },
        "projection": {
            "allowed_node_labels": compiled.descriptor.node_labels,
            "allowed_relationship_types": compiled.descriptor.relationship_types,
            "workspace_scope_required": True,
            "provenance_required": ["_source_id", "_writer_ts", "_writer_agent"],
        },
    }
    # A profile may carry an explicit module-quality boundary. It is metadata
    # for admission and JIT verification, not an implicit rewrite of the
    # profile vocabulary; a narrowed prompt payload must be built as a
    # separately reviewed context slice.
    from .module_scorecard import ModuleQualityPolicy, decide_module_quality, score_module

    declared_specs = module_specs or {}
    unknown_purposes = set(declared_specs) - set(profiles)
    if unknown_purposes:
        raise ValueError(
            "module_specs contains unsupported profile purposes: "
            + ", ".join(sorted(str(item) for item in unknown_purposes))
        )
    for purpose, payload in profiles.items():
        spec = declared_specs.get(purpose, {})
        if not isinstance(spec, Mapping):
            raise ValueError(f"module_specs[{purpose!r}] must be a mapping")
        class_names = spec.get("class_names", payload["allowed_node_labels"])
        required_relations = spec.get(
            "required_relations", payload["allowed_relationship_types"]
        )
        policy_values = spec.get("quality_policy", {})
        if isinstance(class_names, str) or isinstance(required_relations, str):
            raise ValueError(
                f"module_specs[{purpose!r}] class_names and required_relations must be lists"
            )
        if not isinstance(policy_values, Mapping):
            raise ValueError(f"module_specs[{purpose!r}].quality_policy must be a mapping")
        policy = ModuleQualityPolicy(**dict(policy_values))
        scorecard = score_module(
            ontology,
            module_id=str(spec.get("module_id", f"profile:{purpose}")),
            class_names=class_names,
            sibling_modules=spec.get("sibling_modules", ()),
            required_relations=required_relations,
            target_entities=policy.target_entities,
        )
        decision = decide_module_quality(scorecard, policy=policy)
        profile = {
            "schema_version": "seocho.agent_ontology_profile.v1",
            "purpose": purpose,
            "canonical_bundle_sha256": digest,
            "ontology_context_hash": compiled.descriptor.context_hash,
            "module_quality": {
                "scorecard": scorecard.to_dict(),
                "policy": policy.to_dict(),
                "decision": decision.to_dict(),
            },
            **payload,
        }
        profile["profile_sha256"] = hashlib.sha256(
            json.dumps(profile, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        (agent_profiles_dir / f"{purpose}.json").write_text(
            json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return RdfOntologyBundle(
        directory, jsonld_path, turtle_path, shacl_path, manifest_path, agent_profiles_dir, digest
    )
