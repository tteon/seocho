"""Ontology definition, governance, and lifecycle.

Sixteen modules and 7,872 lines used to sit flat in `src/seocho/` as
`ontology.py` plus fifteen `ontology_*.py` siblings — larger than every
subpackage except `query/`, and the second-largest unit in the SDK. That was a
package already; it was spelled with underscores instead of a directory, so it
had no `__init__.py`, no declared surface, and no single answer to "where does
ontology governance live".

Nothing about the public API changed. `from seocho.ontology import Ontology`
resolves exactly as before, and every `seocho.ontology_<name>` module still
imports — those are now four-line re-exports pointing here.

Resolution is lazy, and that is not an optimisation. `core` and
`serialization` / `artifacts` / `versioning` import each other, cycles that
already existed and survive because the imports sit inside methods. An eager
`__init__` would turn them into an ImportError at first touch. The same lazy
`__getattr__` pattern guards `src/seocho/__init__.py` for the same reason.
"""

from __future__ import annotations

import importlib
from typing import Any

# name -> submodule that defines it. Generated from the modules' public
# definitions; a name added to a submodule is not reachable from the package
# root until it is listed here, which is deliberate — the package surface is
# declared, not inferred at import time.
_EXPORTS = {
    "AmbiguityQuarantine": "ambiguity",
    "AmbiguousEntity": "ambiguity",
    "MappingProposal": "ambiguity",
    "SIGNAL_ALIAS_COLLISION": "ambiguity",
    "SIGNAL_FALLBACK": "ambiguity",
    "SIGNAL_OOV": "ambiguity",
    "SIGNAL_OUT_OF_ONTOLOGY": "ambiguity",
    "apply_mapping_spec": "ambiguity",
    "detect_ambiguities": "ambiguity",
    "load_mapping_spec": "ambiguity",
    "proposals_to_mapping_spec": "ambiguity",
    "propose_mappings": "ambiguity",
    "starter_mapping_spec": "ambiguity",
    "ontology_to_approved_artifacts": "artifacts",
    "ontology_to_ontology_candidate": "artifacts",
    "ontology_to_semantic_artifact_draft": "artifacts",
    "ontology_to_semantic_prompt_context": "artifacts",
    "ontology_to_shacl_candidate": "artifacts",
    "ontology_to_vocabulary_candidate": "artifacts",
    "CompiledOntologyContext": "context",
    "OntologyContextCache": "context",
    "OntologyContextDescriptor": "context",
    "OntologyDriftError": "context",
    "apply_anthropic_cache_control": "context",
    "apply_ontology_context_to_graph_payload": "context",
    "assess_graph_ontology_context_status": "context",
    "assess_ontology_context_mismatch": "context",
    "build_ontology_context_summary_query": "context",
    "compile_ontology_context": "context",
    "compile_ontology_context_delta": "context",
    "enforce_drift_policy": "context",
    "merge_ontology_context_metadata": "context",
    "ontology_context_graph_properties": "context",
    "query_ontology_context_mismatch": "context",
    "same_ontology_context_hash": "context",
    "FreshnessSignals": "freshness",
    "FreshnessDecision": "freshness",
    "evaluate_freshness": "freshness",
    "freshness_to_drift_policy": "freshness",
    "BoundaryViolation": "context_map",
    "BoundedContext": "context_map",
    "ContextMap": "context_map",
    "CompiledOntologyProfile": "control_plane",
    "OntologyControlPlane": "control_plane",
    "OntologyProfile": "control_plane",
    "OntologyProfileEvaluation": "control_plane",
    "OntologyProfileRegistry": "control_plane",
    "OntologyProfileSelection": "control_plane",
    "OntologySignal": "control_plane",
    "PromotionBoundaryError": "control_plane",
    "check_promotion_boundaries": "control_plane",
    "Cardinality": "core",
    "ConstraintType": "core",
    "NodeDef": "core",
    "Ontology": "core",
    "P": "core",
    "Property": "core",
    "PropertyType": "core",
    "RelDef": "core",
    "GovernanceValidationResult": "governance",
    "OntologyCheckResult": "governance",
    "OntologyDiffResult": "governance",
    "OntologyGovernanceReport": "governance",
    "Owlready2InspectionResult": "governance",
    "build_ontology_governance_report": "governance",
    "check_ontology": "governance",
    "competency_question_coverage": "governance",
    "competency_question_report": "governance",
    "conformance_score": "governance",
    "diff_ontologies": "governance",
    "export_ontology_payload": "governance",
    "governance_gate": "governance",
    "inspect_owl_ontology": "governance",
    "lint_ontology": "governance",
    "load_competency_questions": "governance",
    "load_ontology_file": "governance",
    "reason_consistency": "governance",
    "validate_rdf_with_pyshacl": "governance",
    "OntologyImportResult": "importers",
    "SUPPORTED_FORMATS": "importers",
    "detect_format": "importers",
    "import_arrows": "importers",
    "import_cypher_ddl": "importers",
    "import_data_importer": "importers",
    "import_document": "importers",
    "import_graphql": "importers",
    "import_linkml": "importers",
    "import_native": "importers",
    "MetaProperties": "ontoclean",
    "OntoCleanResult": "ontoclean",
    "OntoCleanViolation": "ontoclean",
    "build_inference_prompt": "ontoclean",
    "check_ontoclean": "ontoclean",
    "dump_metaproperties": "ontoclean",
    "infer_metaproperties": "ontoclean",
    "load_metaproperties": "ontoclean",
    "resync_ontology": "resync",
    "OntologyEvidenceState": "run_context",
    "OntologyPolicyDecision": "run_context",
    "OntologyRunContext": "run_context",
    "build_local_ontology_run_context": "run_context",
    "build_runtime_ontology_run_context": "run_context",
    "CorpusProfile": "scorecard",
    "DimensionScore": "scorecard",
    "OntologyScorecard": "scorecard",
    "WeakPoint": "scorecard",
    "build_corpus_profile": "scorecard",
    "score_ontology": "scorecard",
    "ontology_from_jsonld_dict": "serialization",
    "ontology_from_jsonld_path": "serialization",
    "ontology_from_ttl": "serialization",
    "ontology_subtract": "serialization",
    "ontology_to_jsonld": "serialization",
    "ontology_to_ttl": "serialization",
    "OntologySlice": "slice",
    "render_slice_extraction_context": "slice",
    "slice_ontology": "slice",
    "OntologySnapshot": "snapshot_store",
    "OntologySnapshotStore": "snapshot_store",
    "SnapshotConflict": "snapshot_store",
    "OntologyUpgradePlan": "versioning",
    "OntologyVersionIdentity": "versioning",
    "build_ontology_upgrade_plan": "versioning",
    "is_valid_semver": "versioning",
    "ontology_schema_fingerprint": "versioning",
    "ontology_version_identity": "versioning",
    "parse_semver": "versioning",
}

_SUBMODULES = frozenset(_EXPORTS.values())


def __getattr__(name: str) -> Any:
    if name in _SUBMODULES:
        return importlib.import_module(f".{name}", __name__)
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f".{module}", __name__), name)


def __dir__() -> list[str]:
    return sorted(set(_EXPORTS) | _SUBMODULES)


__all__ = sorted(_EXPORTS)
