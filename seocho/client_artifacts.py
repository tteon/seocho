from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional, Sequence

if TYPE_CHECKING:
    from .semantic import ApprovedArtifacts, SemanticArtifactDraftInput, SemanticPromptContext


def require_ontology_contract(client: Any, database: Optional[str] = None) -> Any:
    ontology = (
        client.get_ontology(database or client.default_database)
        if database
        else client.ontology
    )
    if ontology is None:
        raise RuntimeError(
            "An ontology is required for this operation. "
            "Provide ontology=... when creating the client or register one for the target database."
        )
    return ontology


def approved_artifacts_from_ontology(
    client: Any,
    *,
    database: Optional[str] = None,
    include_vocabulary: bool = True,
    include_property_terms: bool = True,
) -> "ApprovedArtifacts":
    ontology = require_ontology_contract(client, database)
    return ontology.to_approved_artifacts(
        include_vocabulary=include_vocabulary,
        include_property_terms=include_property_terms,
    )


def artifact_draft_from_ontology(
    client: Any,
    *,
    database: Optional[str] = None,
    name: Optional[str] = None,
    include_vocabulary: bool = True,
    include_property_terms: bool = True,
    source_summary: Optional[Dict[str, Any]] = None,
) -> "SemanticArtifactDraftInput":
    ontology = require_ontology_contract(client, database)
    return ontology.to_semantic_artifact_draft(
        name=name,
        include_vocabulary=include_vocabulary,
        include_property_terms=include_property_terms,
        source_summary=source_summary,
    )


def prompt_context_from_ontology(
    client: Any,
    *,
    database: Optional[str] = None,
    instructions: Optional[Sequence[str]] = None,
    include_vocabulary: bool = True,
    include_property_terms: bool = True,
) -> "SemanticPromptContext":
    ontology = require_ontology_contract(client, database)
    return ontology.to_semantic_prompt_context(
        instructions=instructions,
        include_vocabulary=include_vocabulary,
        include_property_terms=include_property_terms,
    )
