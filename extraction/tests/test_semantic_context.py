import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from semantic_context import build_dynamic_prompt_context


def test_build_dynamic_prompt_context_merges_graph_artifacts_and_developer_overrides():
    context = build_dynamic_prompt_context(
        category="finance",
        source_type="text",
        approved_artifacts={
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
        record_metadata={
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
        graph_metadata={
            "graph_id": "customer360",
            "database": "customer360",
            "ontology_id": "customer",
            "vocabulary_profile": "vocabulary.v2",
            "description": "Customer memory graph",
            "workspace_scope": "default",
        },
    )

    assert context["ontology_name"] == "approved_finance"
    assert "Company" in context["entity_types"]
    assert "RetailAccount" in context["entity_types"]
    assert "ACQUIRED: Company -> Company" in context["relationship_types"]
    assert "Retail Account" in context["vocabulary_terms"]
    assert "Canonical Account" in context["vocabulary_terms"]
    assert "Graph ID: customer360" in context["graph_context"]
    assert "Prefer our internal account taxonomy." in context["developer_instructions"]
    assert "ACME Holdings" in context["entity_guidance"]
