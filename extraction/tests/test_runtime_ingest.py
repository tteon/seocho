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

    def load_data(self, database: str, graph_data: dict, source_id: str, workspace_id: str = "default"):
        self.loaded.append((database, source_id, workspace_id, graph_data))


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
        workspace_id="default",
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
    assert result["semantic_artifacts"]["vocabulary_candidate"]["schema_version"] == "vocabulary.v2"
    assert result["semantic_artifacts"]["vocabulary_candidate"]["profile"] == "skos"
    assert isinstance(result["semantic_artifacts"]["vocabulary_candidate"]["terms"], list)
    assert db.provisioned == ["kgnormal"]
    assert len(db.loaded) == 2
    assert db.loaded[0][2] == "default"
    assert "rule_validation_summary" in db.loaded[0][3]
    doc_nodes = [node for node in db.loaded[0][3]["nodes"] if node.get("label") == "Document"]
    assert len(doc_nodes) == 1
    assert doc_nodes[0]["properties"]["memory_id"] == "r1"
    assert doc_nodes[0]["properties"]["workspace_id"] == "default"
    assert "metadata_json" in doc_nodes[0]["properties"]


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


def test_build_graph_prompt_metadata_uses_registered_graph_target():
    fake_neo4j = types.ModuleType("neo4j")
    fake_neo4j.GraphDatabase = MagicMock()
    fake_neo4j_exceptions = types.ModuleType("neo4j.exceptions")
    fake_neo4j_exceptions.ServiceUnavailable = RuntimeError
    fake_neo4j_exceptions.SessionExpired = RuntimeError
    with patch.dict(sys.modules, {"neo4j": fake_neo4j, "neo4j.exceptions": fake_neo4j_exceptions}):
        runtime_ingest = importlib.import_module("runtime_ingest")
        runtime_ingest = importlib.reload(runtime_ingest)

    with patch.object(runtime_ingest.graph_registry, "find_by_database") as mock_find:
        mock_find.return_value = types.SimpleNamespace(
            graph_id="customer360",
            database="customer360",
            ontology_id="customer",
            vocabulary_profile="vocabulary.v2",
            description="Customer memory graph",
            workspace_scope="default",
        )
        payload = runtime_ingest.RuntimeRawIngestor._build_graph_prompt_metadata("customer360")

    assert payload["graph_id"] == "customer360"
    assert payload["ontology_id"] == "customer"
    assert payload["description"] == "Customer memory graph"
