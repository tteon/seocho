"""
Parity harness: local SDK ↔ server ingest result contract comparison.

This test runs the same ontology + text through BOTH paths (without real
LLM or DB) and compares the result contracts.  It deliberately exposes
every gap between local and server so that each subsequent refactoring
phase can close a specific gap and make one more assertion pass.

The test is structured as a gap inventory — failing assertions are
marked ``xfail`` with the reason.  As phases land, we remove the
``xfail`` markers and the test becomes a regression guard.
"""

import importlib
import sys
import types
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from seocho.ontology import NodeDef, Ontology, P, RelDef


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

SHARED_ONTOLOGY = Ontology(
    name="parity_test",
    version="1.0",
    nodes={
        "Company": NodeDef(
            description="A company",
            properties={"name": P(str, unique=True), "ticker": P(str)},
        ),
        "Person": NodeDef(
            description="A person",
            properties={"name": P(str), "age": P(int)},
        ),
    },
    relationships={
        "WORKS_AT": RelDef(source="Person", target="Company", description="Employment"),
        "ACQUIRED": RelDef(source="Company", target="Company", description="Acquisition"),
    },
)

SHARED_TEXT = "Apple acquired Beats Electronics in 2014. Tim Cook works at Apple."

SHARED_RECORDS = [
    {"id": "rec_1", "content": SHARED_TEXT, "source_type": "text", "category": "general"},
]


# ---------------------------------------------------------------------------
# Fake graph store for local path
# ---------------------------------------------------------------------------

class FakeGraphStore:
    """Captures write() calls without touching a DB."""

    def __init__(self):
        self.written_nodes: List[Dict[str, Any]] = []
        self.written_rels: List[Dict[str, Any]] = []

    def write(self, nodes, relationships, *, database="neo4j", workspace_id="default", source_id="", **kw):
        self.written_nodes.extend(nodes)
        self.written_rels.extend(relationships)
        return {"nodes_created": len(nodes), "relationships_created": len(relationships), "errors": []}

    def query(self, cypher, *, params=None, database="neo4j"):
        return []

    def ensure_constraints(self, ontology, *, database="neo4j"):
        return {"success": 0, "errors": []}

    def execute_write(self, cypher, *, params=None, database="neo4j"):
        return {"nodes_affected": 0, "relationships_affected": 0, "properties_set": 0}

    def get_schema(self, *, database="neo4j"):
        return {"labels": [], "relationship_types": [], "property_keys": []}

    def delete_by_source(self, source_id, *, database="neo4j"):
        return {"nodes_deleted": 0, "relationships_deleted": 0}

    def count_by_source(self, source_id, *, database="neo4j"):
        return {"nodes": 0, "relationships": 0}

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Fake LLM backend for local path
# ---------------------------------------------------------------------------

class FakeEmbeddingBackend:
    """Returns deterministic embeddings for relatedness testing."""

    def embed(self, texts, *, model=None):
        import hashlib
        results = []
        for text in texts:
            h = hashlib.md5(text.encode()).digest()
            vec = [float(b) / 255.0 for b in h] * 96  # 1536-dim
            results.append(vec[:1536])
        return results


class FakeLLM:
    """Returns a deterministic extraction result."""

    def __init__(self):
        self.provider = "fake"
        self.model = "fake-model"

    def complete(self, *, system, user, temperature=0.0, max_tokens=None, response_format=None):
        import json

        class FakeResponse:
            def __init__(self, text):
                self.text = text
                self.model = "fake-model"
                self.usage = {}

            def json(self):
                return json.loads(self.text)

        result = json.dumps({
            "nodes": [
                {"id": "apple", "label": "Company", "properties": {"name": "Apple"}},
                {"id": "beats", "label": "Company", "properties": {"name": "Beats Electronics"}},
                {"id": "tim", "label": "Person", "properties": {"name": "Tim Cook", "age": 63}},
            ],
            "relationships": [
                {"source": "apple", "target": "beats", "type": "ACQUIRED", "properties": {"year": 2014}},
                {"source": "tim", "target": "apple", "type": "WORKS_AT", "properties": {}},
            ],
        })
        return FakeResponse(result)


# ---------------------------------------------------------------------------
# Fake DB manager for server path
# ---------------------------------------------------------------------------

class FakeDbManager:
    def __init__(self):
        self.loaded: List[tuple] = []
        self.provisioned: List[str] = []

    def provision_database(self, name):
        self.provisioned.append(name)

    def load_data(self, database, graph_data, source_id, workspace_id="default"):
        self.loaded.append((database, source_id, workspace_id, graph_data))

    @property
    def driver(self):
        mock = MagicMock()
        mock.session.return_value.__enter__ = MagicMock(return_value=MagicMock(run=MagicMock(return_value=[])))
        mock.session.return_value.__exit__ = MagicMock(return_value=False)
        return mock


# ---------------------------------------------------------------------------
# Result normalization — extract comparable fields from both paths
# ---------------------------------------------------------------------------

def _normalize_local_result(memory_obj) -> Dict[str, Any]:
    """Extract parity-comparable fields from a local Memory return."""
    meta = memory_obj.metadata or {}
    return {
        "nodes_count": meta.get("nodes_created", 0),
        "relationships_count": meta.get("relationships_created", 0),
        "has_rule_profile": meta.get("rule_profile") is not None,
        "has_rule_validation_summary": meta.get("rule_validation_summary") is not None,
        "has_validation_errors": len(meta.get("validation_errors", [])) > 0,
        "fallback_used": False,  # local path has no fallback concept
        "status": memory_obj.status,
    }


def _normalize_server_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Extract parity-comparable fields from server ingest_records return."""
    return {
        "nodes_count": result.get("total_nodes", 0),
        "relationships_count": result.get("total_relationships", 0),
        "has_rule_profile": result.get("rule_profile") is not None,
        "has_rule_validation_summary": any(
            "rule_validation_summary" in (gd or {})
            for _, _, _, gd in getattr(result, "_loaded_graphs", [])
        ) if hasattr(result, "_loaded_graphs") else (result.get("rule_profile") is not None),
        "has_validation_errors": len(result.get("errors", [])) > 0,
        "fallback_used": result.get("fallback_records", 0) > 0,
        "status": result.get("status", ""),
    }


# ---------------------------------------------------------------------------
# Run both paths
# ---------------------------------------------------------------------------

def _run_local_path() -> Dict[str, Any]:
    """Execute the local SDK indexing path."""
    from seocho.index.pipeline import IndexingPipeline

    store = FakeGraphStore()
    llm = FakeLLM()
    embedding = FakeEmbeddingBackend()
    pipeline = IndexingPipeline(
        ontology=SHARED_ONTOLOGY,
        graph_store=store,
        llm=llm,
        workspace_id="default",
        enable_rule_constraints=True,
        embedding_backend=embedding,
    )
    result = pipeline.index(SHARED_TEXT, database="testdb", category="general")
    return {
        "indexing_result": result,
        "written_nodes": store.written_nodes,
        "written_rels": store.written_rels,
    }


def _run_server_path() -> Dict[str, Any]:
    """Execute the server-side RuntimeRawIngestor path (fallback mode, no LLM)."""
    fake_neo4j = types.ModuleType("neo4j")
    fake_neo4j.GraphDatabase = MagicMock()
    fake_neo4j_exceptions = types.ModuleType("neo4j.exceptions")
    fake_neo4j_exceptions.ServiceUnavailable = RuntimeError
    fake_neo4j_exceptions.SessionExpired = RuntimeError

    with patch.dict(sys.modules, {"neo4j": fake_neo4j, "neo4j.exceptions": fake_neo4j_exceptions}):
        runtime_ingest = importlib.import_module("runtime_ingest")
        runtime_ingest = importlib.reload(runtime_ingest)

    db_manager = FakeDbManager()
    ingestor = runtime_ingest.RuntimeRawIngestor(db_manager=db_manager)
    result = ingestor.ingest_records(
        records=SHARED_RECORDS,
        target_database="testdb",
        workspace_id="default",
        enable_rule_constraints=True,
    )
    return {
        "ingest_result": result,
        "loaded_graphs": db_manager.loaded,
    }


# ---------------------------------------------------------------------------
# Parity contract tests
# ---------------------------------------------------------------------------

class TestParityHarness:
    """Compare local vs server ingest result contracts.

    Each test checks one aspect of parity.  ``xfail`` markers document
    known gaps that will be closed by subsequent phases.
    """

    @pytest.fixture(scope="class")
    def local_result(self):
        return _run_local_path()

    @pytest.fixture(scope="class")
    def server_result(self):
        return _run_server_path()

    # --- Both paths produce nodes ---
    def test_both_produce_nodes(self, local_result, server_result):
        local_nodes = len(local_result["written_nodes"])
        server_nodes = server_result["ingest_result"]["total_nodes"]
        assert local_nodes > 0, "local path produced no nodes"
        assert server_nodes > 0, "server path produced no nodes"

    # --- Both paths produce relationships ---
    def test_both_produce_relationships(self, local_result, server_result):
        local_rels = len(local_result["written_rels"])
        server_rels = server_result["ingest_result"]["total_relationships"]
        assert local_rels > 0, "local path produced no relationships"
        assert server_rels > 0, "server path produced no relationships"

    # --- Rule profile present in both ---
    def test_rule_profile_present_in_both(self, local_result, server_result):
        local_rp = local_result["indexing_result"].rule_profile
        server_rp = server_result["ingest_result"].get("rule_profile")
        assert local_rp is not None, "local path missing rule_profile"
        assert server_rp is not None, "server path missing rule_profile"

    # --- Rule profile has same schema_version ---
    def test_rule_profile_schema_version_match(self, local_result, server_result):
        local_rp = local_result["indexing_result"].rule_profile
        server_rp = server_result["ingest_result"].get("rule_profile")
        if local_rp is None or server_rp is None:
            pytest.skip("rule_profile missing from one path")
        assert local_rp.get("schema_version") == server_rp.get("schema_version")

    # --- Both paths report success ---
    def test_both_report_success(self, local_result, server_result):
        assert local_result["indexing_result"].ok, "local path not ok"
        server_status = server_result["ingest_result"]["status"]
        assert server_status in ("success", "success_with_fallback"), f"server status: {server_status}"

    # --- Validation summary present ---
    def test_rule_validation_summary_present(self, local_result, server_result):
        local_vs = local_result["indexing_result"].rule_validation_summary
        # Server embeds validation summary per-record in the loaded graph data
        server_loaded = server_result["loaded_graphs"]
        server_has_vs = any(
            "rule_validation_summary" in (graph_data or {})
            for _, _, _, graph_data in server_loaded
        )
        assert local_vs is not None, "local path missing rule_validation_summary"
        assert server_has_vs, "server path missing rule_validation_summary in loaded graphs"

    # --- Gap inventory: features server has but local doesn't ---

    def test_gap_semantic_artifacts(self, local_result, server_result):
        """Server returns semantic_artifacts; local doesn't."""
        server_artifacts = server_result["ingest_result"].get("semantic_artifacts")
        assert server_artifacts is not None, "expected server to have semantic_artifacts"
        # Local has no equivalent — this is a known gap
        local_has_artifacts = hasattr(local_result["indexing_result"], "semantic_artifacts")
        if not local_has_artifacts:
            pytest.xfail("LOCAL GAP: IndexingResult has no semantic_artifacts field")

    def test_gap_fallback_tracking(self, local_result, server_result):
        """Server tracks fallback_records; local has no fallback concept."""
        server_fallback = server_result["ingest_result"].get("fallback_records", 0)
        # Server used fallback (no LLM in test), local used fake LLM
        assert isinstance(server_fallback, int)
        # Local has no fallback concept — this is a known gap
        local_has_fallback = hasattr(local_result["indexing_result"], "fallback_records")
        if not local_has_fallback:
            pytest.xfail("LOCAL GAP: IndexingResult has no fallback_records tracking")

    def test_embedding_relatedness_present_in_both(self, local_result, server_result):
        """Both paths compute embedding relatedness when a backend is available."""
        server_artifacts = server_result["ingest_result"].get("semantic_artifacts", {})
        server_relatedness = server_artifacts.get("relatedness_summary", {})
        assert server_relatedness.get("total_records", 0) > 0, "server missing relatedness"

        local_relatedness = local_result["indexing_result"].relatedness_summary
        assert local_relatedness is not None, "local missing relatedness_summary"
        assert local_relatedness.get("total_records", 0) > 0, "local relatedness empty"
