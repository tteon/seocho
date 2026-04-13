from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

if TYPE_CHECKING:
    from .ontology import Ontology


def ontology_to_ontology_candidate(ontology: "Ontology") -> Any:
    from .semantic import OntologyCandidate, OntologyClass, OntologyProperty, OntologyRelationship

    classes = [
        OntologyClass(
            name=label,
            description=node.description,
            aliases=list(node.aliases),
            broader=list(node.broader),
            properties=[
                OntologyProperty(
                    name=prop_name,
                    datatype=prop.property_type.value.lower(),
                    description=prop.description,
                    aliases=list(prop.aliases),
                )
                for prop_name, prop in node.properties.items()
            ],
        )
        for label, node in ontology.nodes.items()
    ]

    relationships = [
        OntologyRelationship(
            type=rel_type,
            source=rel.source,
            target=rel.target,
            description=rel.description,
            aliases=list(rel.aliases),
            related=[name for name in (rel.source, rel.target) if name and name != "Any"],
        )
        for rel_type, rel in ontology.relationships.items()
    ]

    return OntologyCandidate(
        ontology_name=ontology.name,
        classes=classes,
        relationships=relationships,
    )


def ontology_to_shacl_candidate(ontology: "Ontology") -> Any:
    from .semantic import ShaclCandidate, ShaclPropertyConstraint, ShaclShape

    shapes: List[ShaclShape] = []
    for shape_payload in ontology.to_shacl().get("shapes", []):
        target_class = str(shape_payload.get("targetClass", "")).removeprefix("seocho:")
        constraints: List[ShaclPropertyConstraint] = []
        for prop in shape_payload.get("properties", []):
            path = str(prop.get("path", "")).removeprefix("seocho:")
            if not path:
                continue

            if "minCount" in prop:
                constraints.append(
                    ShaclPropertyConstraint(
                        path=path,
                        constraint="minCount",
                        params={"value": prop["minCount"]},
                    )
                )
            if "maxCount" in prop:
                constraints.append(
                    ShaclPropertyConstraint(
                        path=path,
                        constraint="maxCount",
                        params={"value": prop["maxCount"]},
                    )
                )
            if "datatype" in prop:
                constraints.append(
                    ShaclPropertyConstraint(
                        path=path,
                        constraint="datatype",
                        params={"value": prop["datatype"]},
                    )
                )
            if prop.get("unique") is True:
                constraints.append(
                    ShaclPropertyConstraint(
                        path=path,
                        constraint="unique",
                        params={"value": True},
                    )
                )

        shapes.append(ShaclShape(target_class=target_class, properties=constraints))

    return ShaclCandidate(shapes=shapes)


def ontology_to_vocabulary_candidate(
    ontology: "Ontology",
    *,
    include_properties: bool = True,
) -> Any:
    from .semantic import VocabularyCandidate, VocabularyTerm

    term_map: Dict[str, VocabularyTerm] = {}

    def merge_term(
        pref_label: str,
        *,
        alt_labels: Optional[Sequence[str]] = None,
        hidden_labels: Optional[Sequence[str]] = None,
        broader: Optional[Sequence[str]] = None,
        related: Optional[Sequence[str]] = None,
        sources: Optional[Sequence[str]] = None,
        definition: str = "",
        examples: Optional[Sequence[str]] = None,
    ) -> None:
        label = pref_label.strip()
        if not label:
            return
        key = label.casefold()
        current = term_map.get(key)
        if current is None:
            current = VocabularyTerm(pref_label=label)
            term_map[key] = current

        def merge_text_list(existing: List[str], values: Optional[Sequence[str]]) -> None:
            seen = {item.casefold() for item in existing}
            for value in values or []:
                text = str(value).strip()
                if text and text.casefold() not in seen:
                    existing.append(text)
                    seen.add(text.casefold())

        merge_text_list(current.alt_labels, alt_labels)
        merge_text_list(current.hidden_labels, hidden_labels)
        merge_text_list(current.broader, broader)
        merge_text_list(current.related, related)
        merge_text_list(current.sources, sources)
        merge_text_list(current.examples, examples)
        if definition and not current.definition:
            current.definition = definition.strip()

    for label, node in ontology.nodes.items():
        merge_term(
            label,
            alt_labels=node.aliases,
            broader=node.broader,
            sources=[node.same_as] if node.same_as else None,
            definition=node.description,
        )
        if include_properties:
            for prop_name, prop in node.properties.items():
                merge_term(
                    prop_name,
                    alt_labels=prop.aliases,
                    hidden_labels=[f"{label}.{prop_name}"],
                    broader=[label],
                    definition=prop.description,
                )

    for rel_type, rel in ontology.relationships.items():
        merge_term(
            rel_type,
            alt_labels=rel.aliases,
            related=[name for name in (rel.source, rel.target) if name and name != "Any"],
            sources=[rel.same_as] if rel.same_as else None,
            definition=rel.description or f"{rel.source} -> {rel.target}",
        )

    return VocabularyCandidate(
        schema_version="vocabulary.v2",
        profile="skos",
        terms=sorted(term_map.values(), key=lambda item: item.pref_label.casefold()),
    )


def ontology_to_approved_artifacts(
    ontology: "Ontology",
    *,
    include_vocabulary: bool = True,
    include_property_terms: bool = True,
) -> Any:
    from .semantic import ApprovedArtifacts

    return ApprovedArtifacts(
        ontology_candidate=ontology_to_ontology_candidate(ontology),
        shacl_candidate=ontology_to_shacl_candidate(ontology),
        vocabulary_candidate=(
            ontology_to_vocabulary_candidate(
                ontology,
                include_properties=include_property_terms,
            )
            if include_vocabulary
            else None
        ),
    )


def ontology_to_semantic_prompt_context(
    ontology: "Ontology",
    *,
    instructions: Optional[Sequence[str]] = None,
    include_vocabulary: bool = True,
    include_property_terms: bool = True,
) -> Any:
    from .semantic import SemanticPromptContext

    runtime_instructions: List[str] = []
    if instructions:
        runtime_instructions.extend(
            str(item).strip() for item in instructions if str(item).strip()
        )
    if ontology.package_id:
        runtime_instructions.append(
            f"Treat ontology package '{ontology.package_id}' version '{ontology.version}' as authoritative."
        )

    return SemanticPromptContext(
        instructions=runtime_instructions,
        ontology_candidate=ontology_to_ontology_candidate(ontology),
        shacl_candidate=ontology_to_shacl_candidate(ontology),
        vocabulary_candidate=(
            ontology_to_vocabulary_candidate(
                ontology,
                include_properties=include_property_terms,
            )
            if include_vocabulary
            else None
        ),
    )


def ontology_to_semantic_artifact_draft(
    ontology: "Ontology",
    *,
    name: Optional[str] = None,
    include_vocabulary: bool = True,
    include_property_terms: bool = True,
    source_summary: Optional[Dict[str, Any]] = None,
) -> Any:
    from .semantic import SemanticArtifactDraftInput

    summary = {
        "source": "ontology",
        "ontology_name": ontology.name,
        "package_id": ontology.package_id,
        "version": ontology.version,
        "graph_model": ontology.graph_model,
        "namespace": ontology.namespace,
        "node_count": len(ontology.nodes),
        "relationship_count": len(ontology.relationships),
    }
    if source_summary:
        summary.update(source_summary)

    return SemanticArtifactDraftInput(
        name=name or f"{ontology.package_id}-{ontology.version}",
        ontology_candidate=ontology_to_ontology_candidate(ontology),
        shacl_candidate=ontology_to_shacl_candidate(ontology),
        vocabulary_candidate=(
            ontology_to_vocabulary_candidate(
                ontology,
                include_properties=include_property_terms,
            )
            if include_vocabulary
            else None
        ),
        source_summary=summary,
    )
