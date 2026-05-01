import json

from seocho.models import GraphTarget
from seocho.query.constraints import SemanticConstraintSliceBuilder
from seocho.query.run_registry import RunMetadataRegistry


def test_constraint_slice_builder_uses_graph_targets_and_artifacts(tmp_path):
    base_dir = tmp_path / "semantic_artifacts"
    workspace_dir = base_dir / "default"
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "sa_demo.json").write_text(
        json.dumps(
            {
                "artifact_id": "sa_demo",
                "workspace_id": "default",
                "name": "customer360",
                "created_at": "2026-04-13T00:00:00+00:00",
                "status": "approved",
                "approved_at": "2026-04-13T00:00:00+00:00",
                "ontology_candidate": {
                    "ontology_name": "customer360",
                    "classes": [
                        {
                            "name": "Company",
                            "aliases": ["Organization"],
                            "properties": [{"name": "name", "datatype": "string"}],
                        }
                    ],
                    "relationships": [
                        {
                            "type": "USES",
                            "source": "Company",
                            "target": "Language",
                            "aliases": ["uses"],
                        }
                    ],
                },
                "shacl_candidate": {
                    "shapes": [
                        {
                            "target_class": "Company",
                            "properties": [
                                {"path": "name", "constraint": "minCount", "params": {"value": 1}}
                            ],
                        }
                    ]
                },
                "vocabulary_candidate": {
                    "schema_version": "vocabulary.v2",
                    "profile": "skos",
                    "terms": [
                        {
                            "canonical": "Organization",
                            "pref_label": "Organization",
                            "alt_labels": ["Org"],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    builder = SemanticConstraintSliceBuilder(
        artifact_base_dir=str(base_dir),
        graph_targets=[
            GraphTarget(
                graph_id="customer360",
                database="kgnormal",
                uri="bolt://localhost:7687",
                ontology_id="customer360",
                vocabulary_profile="vocabulary.v2",
            )
        ],
    )

    constraint_slice = builder.build_for_database("kgnormal", workspace_id="default")

    assert constraint_slice["graph_id"] == "customer360"
    assert constraint_slice["ontology_id"] == "customer360"
    assert constraint_slice["constraint_strength"] == "semantic_layer"
    assert constraint_slice["allowed_labels"] == ["Company"]
    assert constraint_slice["allowed_relationship_types"] == ["USES"]
    assert constraint_slice["relation_aliases"]["uses"] == "USES"
    assert constraint_slice["label_aliases"]["organization"] == "Organization"
    assert constraint_slice["artifact_ids"] == ["sa_demo"]


def test_run_metadata_registry_persists_semantic_run(tmp_path):
    registry_path = tmp_path / "semantic_runs.db"
    registry = RunMetadataRegistry(path=str(registry_path))

    result = registry.record_run(
        question="What is Neo4j connected to?",
        workspace_id="default",
        route="lpg",
        semantic_context={
            "intent": {"intent_id": "relationship_lookup"},
            "support_assessment": {"status": "supported", "reason": "grounded", "coverage": 1.0},
            "strategy_decision": {"executed_mode": "semantic_direct"},
            "reasoning": {"requested": False},
            "evidence_bundle_preview": {
                "grounded_slots": ["source_entity", "relation_paths"],
                "missing_slots": [],
                "selected_triples": [{"source": "Neo4j", "relation": "USES", "target": "Cypher"}],
                "confidence": 0.92,
            },
        },
        lpg_result={"records": [{"source_entity": "Neo4j"}]},
        rdf_result=None,
        response="Neo4j uses Cypher.",
    )

    assert result["recorded"] is True
    assert registry_path.exists()
