import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from semantic_artifact_store import approve_semantic_artifact, save_semantic_artifact
from semantic_vocabulary import ManagedVocabularyResolver


def test_managed_vocabulary_resolver_uses_global_fallback(tmp_path):
    base_dir = str(tmp_path)
    artifact = save_semantic_artifact(
        workspace_id="global",
        name="global_terms",
        ontology_candidate={
            "ontology_name": "global",
            "classes": [],
            "relationships": [],
        },
        shacl_candidate={"shapes": []},
        vocabulary_candidate={
            "schema_version": "vocabulary.v2",
            "profile": "skos",
            "terms": [
                {
                    "pref_label": "GraphRAG",
                    "alt_labels": ["Graph-RAG", "Graph RAG"],
                    "sources": ["manual"],
                }
            ],
        },
        base_dir=base_dir,
    )
    approve_semantic_artifact(
        workspace_id="global",
        artifact_id=artifact["artifact_id"],
        approved_by="reviewer",
        base_dir=base_dir,
    )

    resolver = ManagedVocabularyResolver(base_dir=base_dir, global_workspace_id="global")
    assert resolver.resolve_alias("Graph-RAG", workspace_id="team_a") == "GraphRAG"


def test_managed_vocabulary_summary_reports_counts(tmp_path):
    base_dir = str(tmp_path)
    global_artifact = save_semantic_artifact(
        workspace_id="global",
        name="global_only",
        ontology_candidate={"ontology_name": "g", "classes": [{"name": "DozerDB"}], "relationships": []},
        shacl_candidate={"shapes": []},
        base_dir=base_dir,
    )
    approve_semantic_artifact(
        workspace_id="global",
        artifact_id=global_artifact["artifact_id"],
        approved_by="reviewer",
        base_dir=base_dir,
    )
    workspace_artifact = save_semantic_artifact(
        workspace_id="default",
        name="workspace_only",
        ontology_candidate={"ontology_name": "w", "classes": [{"name": "Neo4j"}], "relationships": []},
        shacl_candidate={"shapes": []},
        base_dir=base_dir,
    )
    approve_semantic_artifact(
        workspace_id="default",
        artifact_id=workspace_artifact["artifact_id"],
        approved_by="reviewer",
        base_dir=base_dir,
    )

    resolver = ManagedVocabularyResolver(base_dir=base_dir, global_workspace_id="global")
    summary = resolver.to_summary("default")

    assert summary["approved_artifact_counts"]["global"] == 1
    assert summary["approved_artifact_counts"]["workspace"] == 1
    assert summary["alias_count"] >= 2
