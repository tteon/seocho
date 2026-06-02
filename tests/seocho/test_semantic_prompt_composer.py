from extraction.semantic_context import build_dynamic_prompt_context as shim_build_dynamic_prompt_context
from seocho.semantic_prompt_composer import (
    DynamicPromptContext,
    build_dynamic_prompt_context,
    compose_dynamic_prompt_context,
)


def _sample_inputs() -> dict:
    return {
        "category": "finance",
        "source_type": "text",
        "approved_artifacts": {
            "ontology_candidate": {
                "ontology_name": "approved_finance",
                "classes": [
                    {
                        "name": "Company",
                        "description": "Corporate entity",
                        "aliases": ["Corporation"],
                        "properties": [{"name": "name", "datatype": "string"}],
                    }
                ],
                "relationships": [
                    {"type": "ACQUIRED", "source": "Company", "target": "Company", "description": "M&A relation"}
                ],
            },
            "vocabulary_candidate": {
                "schema_version": "vocabulary.v2",
                "profile": "skos",
                "terms": [
                    {"pref_label": "Retail Account", "alt_labels": ["Store Account"], "sources": ["approved"]}
                ],
            },
        },
        "record_metadata": {
            "source": "test",
            "semantic_prompt_context": {
                "instructions": [
                    "Prefer our internal account taxonomy.",
                    "Preserve canonical company labels.",
                ],
                "known_entities": ["ACME Holdings"],
                "ontology_candidate": {
                    "classes": [
                        {
                            "name": "RetailAccount",
                            "description": "Customer account",
                            "aliases": ["Account"],
                            "properties": [{"name": "owner", "datatype": "string"}],
                        }
                    ],
                    "relationships": [],
                },
                "vocabulary_candidate": {
                    "terms": [
                        {
                            "pref_label": "Canonical Account",
                            "alt_labels": ["CA"],
                            "sources": ["developer"],
                        }
                    ]
                },
            },
        },
        "graph_metadata": {
            "graph_id": "customer360",
            "database": "customer360",
            "ontology_id": "customer",
            "vocabulary_profile": "vocabulary.v2",
            "description": "Customer memory graph",
            "workspace_scope": "default",
        },
    }


def test_compose_dynamic_prompt_context_returns_typed_model() -> None:
    composed = compose_dynamic_prompt_context(**_sample_inputs())

    assert isinstance(composed, DynamicPromptContext)
    assert composed.ontology_name == "approved_finance"
    assert "Company" in composed.entity_types
    assert "Canonical Account" in composed.vocabulary_terms
    assert "Graph ID: customer360" in composed.graph_context


def test_build_dynamic_prompt_context_matches_legacy_shim() -> None:
    inputs = _sample_inputs()

    assert build_dynamic_prompt_context(**inputs) == shim_build_dynamic_prompt_context(**inputs)
