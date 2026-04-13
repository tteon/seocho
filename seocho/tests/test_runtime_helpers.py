from seocho.index.runtime_artifacts import (
    build_vocabulary_candidate,
    merge_ontology_candidates,
    merge_rule_profiles,
    merge_shacl_candidates,
    resolve_semantic_artifacts,
    shacl_candidates_to_rule_profile,
    summarize_relatedness,
)
from seocho.index.runtime_memory import ensure_memory_graph


def test_ensure_memory_graph_adds_document_scope_and_mentions_edges():
    graph_data = {
        "nodes": [
            {"id": "company-1", "label": "Company", "properties": {"name": "ACME"}},
            {"id": "person-1", "label": "Person", "properties": {"name": "Alice"}},
        ],
        "relationships": [{"source": "person-1", "target": "company-1", "type": "WORKS_AT"}],
        "_semantic": {"stage": "linked"},
    }

    result = ensure_memory_graph(
        graph_data=graph_data,
        source_id="rec-1",
        workspace_id="default",
        text="Alice works at ACME.",
        category="general",
        source_type="text",
        record_metadata={
            "source_id": "rec-1",
            "user_id": "u-1",
            "session_id": "s-1",
            "created_at": "2026-04-13T00:00:00+00:00",
            "updated_at": "2026-04-13T00:00:00+00:00",
        },
    )

    document_nodes = [node for node in result["nodes"] if node["label"] == "Document"]
    assert len(document_nodes) == 1
    document_props = document_nodes[0]["properties"]
    assert document_props["memory_id"] == "rec-1"
    assert document_props["workspace_id"] == "default"
    assert document_props["user_id"] == "u-1"
    assert document_props["session_id"] == "s-1"

    entity_nodes = [node for node in result["nodes"] if node["label"] != "Document"]
    assert all(node["properties"]["workspace_id"] == "default" for node in entity_nodes)
    assert result["_semantic"]["record_context"]["source_id"] == "rec-1"

    mention_edges = [rel for rel in result["relationships"] if rel["type"] == "MENTIONS"]
    assert len(mention_edges) == 2
    assert all(rel["source"] == "rec-1_doc" for rel in mention_edges)


def test_runtime_artifact_helpers_merge_candidates_and_build_vocabulary():
    merged_ontology = merge_ontology_candidates(
        [
            {
                "ontology_name": "finance",
                "classes": [
                    {
                        "name": "Company",
                        "description": "Public company",
                        "aliases": ["Issuer"],
                        "properties": [{"name": "ticker", "aliases": ["symbol"]}],
                    }
                ],
                "relationships": [{"type": "ACQUIRED", "source": "Company", "target": "Company"}],
            },
            {
                "ontology_name": "finance_secondary",
                "classes": [
                    {
                        "name": "Company",
                        "aliases": ["Company"],
                        "properties": [{"name": "ticker", "aliases": ["Ticker"]}],
                    }
                ],
                "relationships": [{"type": "ACQUIRED", "source": "Company", "target": "Company", "aliases": ["BOUGHT"]}],
            },
        ]
    )
    merged_shacl = merge_shacl_candidates(
        [
            {
                "shapes": [
                    {
                        "target_class": "Company",
                        "properties": [{"path": "ticker", "constraint": "required", "params": {}}],
                    }
                ]
            },
            {
                "shapes": [
                    {
                        "target_class": "Company",
                        "properties": [{"path": "ticker", "constraint": "required", "params": {}}],
                    }
                ]
            },
        ]
    )

    vocabulary = build_vocabulary_candidate(
        merged_ontology,
        merged_shacl,
        prepared_graphs=[
            {
                "nodes": [{"id": "acme", "label": "Company", "properties": {"name": "ACME"}}],
                "relationships": [{"source": "acme", "target": "beta", "type": "ACQUIRED"}],
            }
        ],
    )

    company_class = next(cls for cls in merged_ontology["classes"] if cls["name"] == "Company")
    assert company_class["properties"][0]["aliases"] == ["symbol", "Ticker"]
    acquired_rel = next(rel for rel in merged_ontology["relationships"] if rel["type"] == "ACQUIRED")
    assert acquired_rel["aliases"] == ["BOUGHT"]
    assert merged_shacl["shapes"][0]["properties"] == [{"path": "ticker", "constraint": "required", "params": {}}]
    assert vocabulary["profile"] == "skos"
    assert any(term["pref_label"] == "Company" for term in vocabulary["terms"])
    assert any(term["pref_label"] == "ACQUIRED" for term in vocabulary["terms"])


def test_runtime_artifact_helpers_merge_rule_profiles_and_relatedness_summary():
    shacl_profile = shacl_candidates_to_rule_profile(
        {
            "shapes": [
                {
                    "target_class": "Company",
                    "properties": [
                        {"path": "ticker", "constraint": "required", "params": {}},
                        {"path": "revenue", "constraint": "datatype", "params": {"datatype": "float"}},
                    ],
                }
            ]
        }
    )
    merged = merge_rule_profiles(
        shacl_profile,
        {
            "schema_version": "rules.v1",
            "rules": [
                {"label": "Company", "property_name": "ticker", "kind": "required", "params": {}},
                {"label": "Company", "property_name": "region", "kind": "enum", "params": {"choices": ["US"]}},
            ],
        },
    )
    summary = summarize_relatedness(
        [
            {"is_related": True, "score": 0.9, "embedding_score": 0.9},
            {"is_related": False, "score": 0.1, "embedding_score": None},
        ]
    )

    assert len(merged["rules"]) == 3
    assert summary == {
        "total_records": 2,
        "related_records": 1,
        "unrelated_records": 1,
        "average_score": 0.5,
        "embedding_evaluated_records": 1,
    }


def test_resolve_semantic_artifacts_preserves_policy_contract():
    draft_ontology = {"ontology_name": "draft", "classes": [{"name": "Company"}], "relationships": []}
    draft_shacl = {"shapes": [{"target_class": "Company", "properties": []}]}
    approved_artifacts = {"ontology_candidate": draft_ontology, "shacl_candidate": draft_shacl}

    active_auto, decision_auto = resolve_semantic_artifacts("auto", draft_ontology, draft_shacl, {})
    active_draft, decision_draft = resolve_semantic_artifacts("draft_only", draft_ontology, draft_shacl, {})
    active_approved, decision_approved = resolve_semantic_artifacts(
        "approved_only",
        draft_ontology,
        draft_shacl,
        approved_artifacts,
    )

    assert active_auto["ontology_candidate"]["ontology_name"] == "draft"
    assert decision_auto["status"] == "auto_applied"
    assert active_draft["ontology_candidate"]["classes"] == []
    assert decision_draft["status"] == "draft_pending_review"
    assert active_approved["shacl_candidate"]["shapes"][0]["target_class"] == "Company"
    assert decision_approved["status"] == "approved_applied"
