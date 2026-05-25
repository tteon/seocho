"""Compatibility shim for semantic prompt context composition.

Canonical ownership lives in :mod:`seocho.semantic_prompt_composer`.
"""

from seocho.semantic_prompt_composer import (
    DynamicPromptContext,
    build_dynamic_prompt_context,
    compose_dynamic_prompt_context,
    _clean_list,
    _merge_ontology_candidates,
    _merge_shacl_candidates,
    _merge_string_lists,
    _merge_text_blocks,
    _merge_vocabulary_candidates,
    _normalize_known_entities,
    _normalize_semantic_prompt_context,
    _render_context_notes,
    _render_developer_instructions,
    _render_entity_guidance,
    _render_entity_types,
    _render_graph_context,
    _render_known_entities,
    _render_relationship_types,
    _render_shacl_constraints,
    _render_vocabulary_terms,
)

__all__ = [
    "DynamicPromptContext",
    "build_dynamic_prompt_context",
    "compose_dynamic_prompt_context",
    "_clean_list",
    "_merge_ontology_candidates",
    "_merge_shacl_candidates",
    "_merge_string_lists",
    "_merge_text_blocks",
    "_merge_vocabulary_candidates",
    "_normalize_known_entities",
    "_normalize_semantic_prompt_context",
    "_render_context_notes",
    "_render_developer_instructions",
    "_render_entity_guidance",
    "_render_entity_types",
    "_render_graph_context",
    "_render_known_entities",
    "_render_relationship_types",
    "_render_shacl_constraints",
    "_render_vocabulary_terms",
]
