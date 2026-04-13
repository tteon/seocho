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


def test_embedding_cache_lru_eviction():
    """LRU cache evicts oldest entry when max_size is exceeded."""
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
    ingestor._embedding_cache_max_size = 3

    ingestor._cache_put("a", [1.0, 2.0])
    ingestor._cache_put("b", [3.0, 4.0])
    ingestor._cache_put("c", [5.0, 6.0])
    assert len(ingestor._embedding_cache) == 3

    # Adding 4th entry should evict "a" (oldest)
    ingestor._cache_put("d", [7.0, 8.0])
    assert len(ingestor._embedding_cache) == 3
    assert ingestor._cache_get("a") is None
    assert ingestor._cache_get("d") == [7.0, 8.0]


def test_embedding_cache_lru_freshness():
    """Accessing an entry via _cache_get refreshes it, preventing eviction."""
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
    ingestor._embedding_cache_max_size = 3

    ingestor._cache_put("a", [1.0])
    ingestor._cache_put("b", [2.0])
    ingestor._cache_put("c", [3.0])

    # Access "a" to refresh it — now "b" is oldest
    assert ingestor._cache_get("a") == [1.0]

    # Adding "d" should evict "b" (now oldest), not "a"
    ingestor._cache_put("d", [4.0])
    assert ingestor._cache_get("b") is None
    assert ingestor._cache_get("a") == [1.0]
    assert ingestor._cache_get("d") == [4.0]


def test_parallel_extraction_preserves_order():
    """Batch parallelization returns results in correct input order."""
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

    records = [
        {"id": f"rec_{i}", "content": f"Entity{i} is notable.", "source_type": "text"}
        for i in range(6)
    ]
    result = ingestor.ingest_records(
        records=records,
        target_database="kgnormal",
        workspace_id="default",
        enable_rule_constraints=False,
    )

    assert result["records_processed"] == 6
    assert result["records_failed"] == 0
    # Verify order: loaded source_ids match input order
    loaded_source_ids = [src_id for _, src_id, _, _ in db.loaded]
    assert loaded_source_ids == [f"rec_{i}" for i in range(6)]


def test_parallel_extraction_error_isolation():
    """One failed record does not affect other records in a batch."""
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

    records = [
        {"id": "good1", "content": "Alice is a person.", "source_type": "text"},
        {"id": "bad", "content": "", "source_type": "text"},  # empty content
        {"id": "good2", "content": "Bob is a person.", "source_type": "text"},
    ]
    result = ingestor.ingest_records(
        records=records,
        target_database="kgnormal",
        workspace_id="default",
        enable_rule_constraints=False,
    )

    assert result["records_processed"] == 2
    assert result["records_failed"] == 1
    loaded_source_ids = [src_id for _, src_id, _, _ in db.loaded]
    assert "good1" in loaded_source_ids
    assert "good2" in loaded_source_ids
