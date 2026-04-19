from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping

import yaml


_SUPPORTED_GRAPH_MODELS = {"lpg", "rdf", "hybrid"}
_SUPPORTED_STORAGE_TARGETS = {"ladybug", "neo4j", "dozerdb"}
_ALLOWED_EXTRACTION_STRATEGIES = {"general", "domain", "multi_pass"}
_ALLOWED_LINKING_STRATEGIES = {"llm", "embedding", "none"}
_ALLOWED_VALIDATION_MODES = {"reject", "retry", "relax", "warn"}
_ALLOWED_INFERENCE_MODES = {"base", "deductive", "abductive"}
_ALLOWED_RDF_MODES = {"neo4j_labels", "rdf_overlay"}
_ALLOWED_METRIC_MODELS = {"node", "property"}
_ALLOWED_PROVENANCE_MODES = {"none", "source", "temporal", "full"}


def _string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _dict(value: Any, *, field_name: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a mapping.")
    return dict(value)


@dataclass(slots=True)
class IndexingOntologyBinding:
    """Ontology binding required by an indexing design spec."""

    required: bool = True
    profile: str = ""
    ontology_id: str = ""
    package_id: str = ""
    path: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "IndexingOntologyBinding":
        return cls(
            required=bool(payload.get("required", True)),
            profile=_string(payload.get("profile")),
            ontology_id=_string(payload.get("ontology_id")),
            package_id=_string(payload.get("package_id")),
            path=_string(payload.get("path")),
        )

    def validate(self) -> None:
        if not self.required:
            return
        if any((self.profile, self.ontology_id, self.package_id, self.path)):
            return
        raise ValueError(
            "Indexing design specs must declare an ontology binding. "
            "Add ontology.profile, ontology_id, package_id, or path."
        )

    def resolved_profile(self) -> str:
        return self.profile or "default"


@dataclass(slots=True)
class IndexingDesignSpec:
    """YAML-backed indexing contract for LPG, RDF, and hybrid graph paths."""

    name: str
    graph_model: str
    storage_target: str
    ontology: IndexingOntologyBinding
    description: str = ""
    ingestion: Dict[str, Any] = field(default_factory=dict)
    materialization: Dict[str, Any] = field(default_factory=dict)
    reasoning_cycle: Dict[str, Any] = field(default_factory=dict)
    constraints: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "IndexingDesignSpec":
        if not isinstance(payload, Mapping):
            raise ValueError("Indexing design spec must be a mapping.")
        if "ontology" not in payload:
            raise ValueError("Indexing design spec requires an 'ontology' section.")

        spec = cls(
            name=_string(payload.get("name")),
            graph_model=_string(payload.get("graph_model")).lower(),
            storage_target=_string(payload.get("storage_target")).lower(),
            description=_string(payload.get("description")),
            ontology=IndexingOntologyBinding.from_dict(_dict(payload.get("ontology"), field_name="ontology")),
            ingestion=_dict(payload.get("ingestion"), field_name="ingestion"),
            materialization=_dict(payload.get("materialization"), field_name="materialization"),
            reasoning_cycle=_dict(payload.get("reasoning_cycle"), field_name="reasoning_cycle"),
            constraints=_dict(payload.get("constraints"), field_name="constraints"),
            metadata=_dict(payload.get("metadata"), field_name="metadata"),
        )
        spec.validate()
        return spec

    @classmethod
    def from_yaml(cls, path: str | Path) -> "IndexingDesignSpec":
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
        return cls.from_dict(payload)

    def validate(self) -> None:
        if not self.name:
            raise ValueError("Indexing design spec requires a non-empty name.")
        if self.graph_model not in _SUPPORTED_GRAPH_MODELS:
            raise ValueError(
                "graph_model must be one of: "
                f"{', '.join(sorted(_SUPPORTED_GRAPH_MODELS))}."
            )
        if self.storage_target not in _SUPPORTED_STORAGE_TARGETS:
            raise ValueError(
                "storage_target must be one of: "
                f"{', '.join(sorted(_SUPPORTED_STORAGE_TARGETS))}."
            )
        self.ontology.validate()

        extraction_strategy = _string(self.ingestion.get("extraction_strategy")).lower()
        if extraction_strategy and extraction_strategy not in _ALLOWED_EXTRACTION_STRATEGIES:
            raise ValueError(
                "ingestion.extraction_strategy must be one of: "
                f"{', '.join(sorted(_ALLOWED_EXTRACTION_STRATEGIES))}."
            )

        linking_strategy = _string(self.ingestion.get("linking_strategy")).lower()
        if linking_strategy and linking_strategy not in _ALLOWED_LINKING_STRATEGIES:
            raise ValueError(
                "ingestion.linking_strategy must be one of: "
                f"{', '.join(sorted(_ALLOWED_LINKING_STRATEGIES))}."
            )

        validation_on_fail = _string(self.ingestion.get("validation_on_fail")).lower()
        if validation_on_fail and validation_on_fail not in _ALLOWED_VALIDATION_MODES:
            raise ValueError(
                "ingestion.validation_on_fail must be one of: "
                f"{', '.join(sorted(_ALLOWED_VALIDATION_MODES))}."
            )

        inference_mode = _string(self.ingestion.get("inference_mode")).lower()
        if inference_mode and inference_mode not in _ALLOWED_INFERENCE_MODES:
            raise ValueError(
                "ingestion.inference_mode must be one of: "
                f"{', '.join(sorted(_ALLOWED_INFERENCE_MODES))}."
            )

        rdf_mode = _string(self.materialization.get("rdf_mode")).lower()
        if self.graph_model == "rdf" and not rdf_mode:
            raise ValueError(
                "RDF indexing design specs must declare materialization.rdf_mode."
            )
        if rdf_mode and rdf_mode not in _ALLOWED_RDF_MODES:
            raise ValueError(
                "materialization.rdf_mode must be one of: "
                f"{', '.join(sorted(_ALLOWED_RDF_MODES))}."
            )

        metric_model = _string(self.materialization.get("metric_model")).lower()
        if metric_model and metric_model not in _ALLOWED_METRIC_MODELS:
            raise ValueError(
                "materialization.metric_model must be one of: "
                f"{', '.join(sorted(_ALLOWED_METRIC_MODELS))}."
            )

        provenance = _string(self.materialization.get("provenance_mode")).lower()
        if provenance and provenance not in _ALLOWED_PROVENANCE_MODES:
            raise ValueError(
                "materialization.provenance_mode must be one of: "
                f"{', '.join(sorted(_ALLOWED_PROVENANCE_MODES))}."
            )

        if self.reasoning_cycle and not isinstance(self.reasoning_cycle, dict):
            raise ValueError("reasoning_cycle must be a mapping.")
        enabled = self.reasoning_cycle.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            raise ValueError("reasoning_cycle.enabled must be a boolean.")
        anomaly_sources = self.reasoning_cycle.get("anomaly_sources")
        if anomaly_sources is not None:
            if not isinstance(anomaly_sources, list) or not all(isinstance(item, str) and item.strip() for item in anomaly_sources):
                raise ValueError("reasoning_cycle.anomaly_sources must be a list of strings.")
        for section in ("abduction", "deduction", "induction", "promotion"):
            value = self.reasoning_cycle.get(section)
            if value is not None and not isinstance(value, dict):
                raise ValueError(f"reasoning_cycle.{section} must be a mapping.")

        require_workspace_id = self.constraints.get("require_workspace_id")
        if require_workspace_id is not None and not isinstance(require_workspace_id, bool):
            raise ValueError("constraints.require_workspace_id must be a boolean.")

    def materialize_ontology(self, ontology: Any) -> Any:
        from .ontology import Ontology

        if ontology is None:
            raise ValueError("Indexing design materialization requires an ontology object.")
        if not isinstance(ontology, Ontology):
            raise ValueError("Indexing design materialization expects a seocho.Ontology.")

        payload = ontology.to_dict()
        payload["graph_model"] = self.graph_model
        return Ontology.from_dict(payload)

    def requires_workspace_id(self) -> bool:
        return bool(self.constraints.get("require_workspace_id", False))

    def default_strict_validation(self) -> bool:
        if bool(self.ingestion.get("strict_validation", False)):
            return True
        return _string(self.ingestion.get("validation_on_fail")).lower() == "reject"

    def reasoning_cycle_enabled(self) -> bool:
        return bool(self.reasoning_cycle.get("enabled", False))

    def indexing_metadata(self) -> Dict[str, Any]:
        return {
            "indexing_design": {
                "name": self.name,
                "graph_model": self.graph_model,
                "storage_target": self.storage_target,
                "ontology_profile": self.ontology.resolved_profile(),
                "inference_mode": _string(self.ingestion.get("inference_mode")).lower() or "base",
                "materialization": dict(self.materialization),
                "reasoning_cycle": dict(self.reasoning_cycle),
                "constraints": dict(self.constraints),
                "metadata": dict(self.metadata),
            }
        }

    def default_extraction_prompt(self) -> Any | None:
        if self.graph_model != "lpg":
            return None

        from .query import PromptTemplate

        provenance_mode = _string(self.materialization.get("provenance_mode")).lower() or "source"
        inference_mode = _string(self.ingestion.get("inference_mode")).lower() or "base"
        reasoning_lines = ""
        if self.reasoning_cycle_enabled():
            anomaly_sources = ", ".join(self.reasoning_cycle.get("anomaly_sources", []) or [])
            reasoning_lines = (
                "\nReasoning cycle contract:\n"
                f"- anomaly sources: {anomaly_sources or 'shacl_violation, unsupported_answer'}\n"
                "- abduction must stay candidate-only; annotate hypothetical outputs with "
                '"is_hypothetical": true and "inference_type": "abductive".\n'
                "- deduction should emit only testable graph predictions, not unsupported facts.\n"
                "- induction requires source-grounded evidence before any fact is treated as confirmed.\n"
            )

        return PromptTemplate(
            system=(
                "You are an ontology-aligned property graph extraction system.\n"
                'You are working with the "{{ontology_name}}" ontology.\n\n'
                "Extract entities of the following types:\n{{entity_types}}\n\n"
                "Extract relationships of the following types:\n{{relationship_types}}\n\n"
                "Property graph rules:\n"
                "- Prefer source-grounded scalar attributes as node or edge properties.\n"
                "- Keep repeated or period-specific metrics as separate nodes instead of overwriting one property.\n"
                "- Preserve provenance-friendly properties when available, especially source_span, period, confidence, and extractor_confidence.\n"
                f"- Current provenance mode: {provenance_mode}.\n"
                f"- Current inference mode: {inference_mode}.\n"
                f"{reasoning_lines}\n"
                "{{constraints_summary}}\n\n"
                'Return JSON with "nodes" and "relationships" keys.\n'
                'Nodes: {"id": "unique_id", "label": "EntityType", "properties": {...}}\n'
                'Relationships: {"source": "id", "target": "id", "type": "TYPE", "properties": {...}}'
            ),
            user="Text to extract:\n{{text}}",
        )

    def apply_add_defaults(
        self,
        *,
        metadata: Dict[str, Any] | None,
        strict_validation: bool,
    ) -> Dict[str, Any]:
        merged_metadata: Dict[str, Any] = dict(metadata or {})
        existing_design = merged_metadata.get("indexing_design")
        if isinstance(existing_design, dict):
            merged_metadata["indexing_design"] = {
                **self.indexing_metadata()["indexing_design"],
                **existing_design,
            }
        else:
            merged_metadata.update(self.indexing_metadata())

        return {
            "metadata": merged_metadata or None,
            "strict_validation": bool(strict_validation or self.default_strict_validation()),
        }

    def client_kwargs(self, *, ontology: Any) -> Dict[str, Any]:
        return {
            "ontology": self.materialize_ontology(ontology),
            "ontology_profile": self.ontology.resolved_profile(),
            "extraction_prompt": self.default_extraction_prompt(),
        }


def load_indexing_design_spec(path: str | Path) -> IndexingDesignSpec:
    """Load and validate a YAML indexing design spec."""

    return IndexingDesignSpec.from_yaml(path)


__all__ = [
    "IndexingDesignSpec",
    "IndexingOntologyBinding",
    "load_indexing_design_spec",
]
