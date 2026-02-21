import importlib
import sys
import types
from unittest.mock import MagicMock, patch


class _FakeDbManager:
    def __init__(self):
        self.loaded = []
        self.provisioned = []

    def provision_database(self, name: str):
        self.provisioned.append(name)

    def load_data(self, database: str, graph_data: dict, source_id: str):
        self.loaded.append((database, source_id, graph_data))


def test_runtime_ingest_batches_rule_profile_across_multiple_records():
    fake_neo4j = types.ModuleType("neo4j")
    fake_neo4j.GraphDatabase = MagicMock()
    fake_neo4j_exceptions = types.ModuleType("neo4j.exceptions")
    fake_neo4j_exceptions.ServiceUnavailable = RuntimeError
    fake_neo4j_exceptions.SessionExpired = RuntimeError

    with patch.dict(sys.modules, {"neo4j": fake_neo4j, "neo4j.exceptions": fake_neo4j_exceptions}):
        runtime_ingest = importlib.import_module("runtime_ingest")
        runtime_ingest = importlib.reload(runtime_ingest)

    db = _FakeDbManager()
    ingestor = runtime_ingest.RuntimeRawIngestor(db_manager=db)

    result = ingestor.ingest_records(
        records=[
            {"id": "r1", "content": "ACME acquired Beta in 2024.", "source_type": "text"},
            {
                "id": "r2",
                "source_type": "csv",
                "content": "company,partner\nBeta,ACME\nGamma,Delta\n",
            },
        ],
        target_database="kgnormal",
        enable_rule_constraints=True,
        create_database_if_missing=True,
        semantic_artifact_policy="auto",
    )

    assert result["records_processed"] == 2
    assert result["records_failed"] == 0
    assert result["rule_profile"] is not None
    assert len(result["rule_profile"]["rules"]) > 0
    assert result["semantic_artifacts"] is not None
    assert "relatedness_summary" in result["semantic_artifacts"]
    assert result["semantic_artifacts"]["relatedness_summary"]["total_records"] == 2
    assert result["semantic_artifacts"]["artifact_decision"]["status"] == "auto_applied"
    assert db.provisioned == ["kgnormal"]
    assert len(db.loaded) == 2
    assert "rule_validation_summary" in db.loaded[0][2]


def test_resolve_semantic_artifacts_policy_variants():
    fake_neo4j = types.ModuleType("neo4j")
    fake_neo4j.GraphDatabase = MagicMock()
    fake_neo4j_exceptions = types.ModuleType("neo4j.exceptions")
    fake_neo4j_exceptions.ServiceUnavailable = RuntimeError
    fake_neo4j_exceptions.SessionExpired = RuntimeError
    with patch.dict(sys.modules, {"neo4j": fake_neo4j, "neo4j.exceptions": fake_neo4j_exceptions}):
        runtime_ingest = importlib.import_module("runtime_ingest")
        runtime_ingest = importlib.reload(runtime_ingest)

    draft_ontology = {"ontology_name": "d", "classes": [{"name": "Company"}], "relationships": []}
    draft_shacl = {"shapes": [{"target_class": "Company", "properties": []}]}

    active_auto, decision_auto = runtime_ingest.RuntimeRawIngestor._resolve_semantic_artifacts(
        policy="auto",
        draft_ontology=draft_ontology,
        draft_shacl=draft_shacl,
        approved_artifacts={},
    )
    assert active_auto["ontology_candidate"]["ontology_name"] == "d"
    assert decision_auto["status"] == "auto_applied"

    active_draft, decision_draft = runtime_ingest.RuntimeRawIngestor._resolve_semantic_artifacts(
        policy="draft_only",
        draft_ontology=draft_ontology,
        draft_shacl=draft_shacl,
        approved_artifacts={},
    )
    assert active_draft["ontology_candidate"]["classes"] == []
    assert decision_draft["status"] == "draft_pending_review"

    active_approved, decision_approved = runtime_ingest.RuntimeRawIngestor._resolve_semantic_artifacts(
        policy="approved_only",
        draft_ontology=draft_ontology,
        draft_shacl=draft_shacl,
        approved_artifacts={"ontology_candidate": draft_ontology, "shacl_candidate": draft_shacl},
    )
    assert active_approved["shacl_candidate"]["shapes"][0]["target_class"] == "Company"
    assert decision_approved["status"] == "approved_applied"
