from __future__ import annotations

from seocho import NodeDef, Ontology, P, RelDef
from seocho.client import _LocalEngine
from seocho.index.pipeline import IndexingPipeline
from seocho.ontology_context import (
    OntologyContextCache,
    assess_graph_ontology_context_status,
    assess_ontology_context_mismatch,
    build_ontology_context_summary_query,
    compile_ontology_context,
    query_ontology_context_mismatch,
)


def _ontology(version: str = "1.0.0") -> Ontology:
    return Ontology(
        name="finance",
        package_id="company-finance",
        version=version,
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "FinancialMetric": NodeDef(properties={"name": P(str), "value": P(str)}),
        },
        relationships={
            "REPORTED": RelDef(source="Company", target="FinancialMetric"),
        },
    )


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return dict(self._payload)


class _FakeLLM:
    model = "fake-model"

    def complete(self, *, system, user, temperature, response_format=None):  # noqa: ANN001
        return _FakeResponse(
            {
                "nodes": [
                    {"id": "acme", "label": "Company", "properties": {"name": "ACME"}},
                    {
                        "id": "revenue",
                        "label": "FinancialMetric",
                        "properties": {"name": "Revenue", "value": "10"},
                    },
                ],
                "relationships": [
                    {"source": "acme", "target": "revenue", "type": "REPORTED", "properties": {}}
                ],
            }
        )


class _FakeGraphStore:
    def __init__(self) -> None:
        self.writes = []

    def write(self, nodes, relationships, *, database="neo4j", workspace_id="default", source_id=""):  # noqa: ANN001
        self.writes.append(
            {
                "nodes": nodes,
                "relationships": relationships,
                "database": database,
                "workspace_id": workspace_id,
                "source_id": source_id,
            }
        )
        return {
            "nodes_created": len(nodes),
            "relationships_created": len(relationships),
            "errors": [],
        }


class _MismatchGraphStore(_FakeGraphStore):
    def __init__(self) -> None:
        super().__init__()
        self.queries = []

    def query(self, cypher: str, *, params=None, database="neo4j"):  # noqa: ANN001
        self.queries.append(cypher)
        return [
            {
                "indexed_context_hashes": ["old-context-hash"],
                "scoped_nodes": 3,
                "missing_context_nodes": 1,
            }
        ]


def test_compile_ontology_context_has_stable_identity() -> None:
    first = compile_ontology_context(
        _ontology(),
        workspace_id="acme",
        profile="finder-financials",
    )
    second = compile_ontology_context(
        _ontology(),
        workspace_id="acme",
        profile="finder-financials",
    )
    changed = compile_ontology_context(
        _ontology(version="1.1.0"),
        workspace_id="acme",
        profile="finder-financials",
    )

    assert first.descriptor.context_hash == second.descriptor.context_hash
    assert first.descriptor.context_hash != changed.descriptor.context_hash
    assert first.descriptor.ontology_id == "company-finance"
    assert first.descriptor.profile == "finder-financials"
    assert first.descriptor.glossary_term_count >= 3
    assert first.descriptor.glossary_hash
    assert "financial_metric_lookup" in first.descriptor.deterministic_intents


def test_glossary_aliases_are_part_of_context_identity() -> None:
    base = _ontology()
    aliased = _ontology()
    aliased.nodes["Company"].aliases.append("Issuer")

    base_context = compile_ontology_context(base)
    aliased_context = compile_ontology_context(aliased)

    assert base_context.descriptor.glossary_hash != aliased_context.descriptor.glossary_hash
    assert base_context.descriptor.context_hash != aliased_context.descriptor.context_hash


def test_ontology_context_cache_tracks_hits() -> None:
    ontology = _ontology()
    cache = OntologyContextCache(max_size=2)

    first = cache.get(ontology, workspace_id="acme", profile="default")
    second = cache.get(ontology, workspace_id="acme", profile="default")

    assert first is second
    assert cache.stats()["hits"] == 1
    assert cache.stats()["misses"] == 1


def test_indexing_result_records_ontology_context() -> None:
    graph_store = _FakeGraphStore()
    pipeline = IndexingPipeline(
        ontology=_ontology(),
        graph_store=graph_store,
        llm=_FakeLLM(),
        workspace_id="acme",
        ontology_profile="finder-financials",
    )

    result = pipeline.index("ACME reported revenue of 10.", database="neo4j")
    payload = result.to_dict()

    assert result.ok is True
    assert payload["ontology_context"]["workspace_id"] == "acme"
    assert payload["ontology_context"]["profile"] == "finder-financials"
    assert payload["ontology_context"]["context_hash"]
    assert payload["semantic_package"]["package_id"]
    assert payload["semantic_package"]["package_hash"]
    assert payload["semantic_package"]["ontology_id"] == "company-finance"
    assert payload["semantic_package"]["ontology_profile"] == "finder-financials"
    assert payload["stage_metrics"]["total_ms"] >= 0.0
    assert payload["policy_metrics"]["mode"] == "indexing"
    assert payload["policy_metrics"]["chunks_processed"] == 1
    written_node = graph_store.writes[0]["nodes"][0]
    written_rel = graph_store.writes[0]["relationships"][0]
    assert written_node["properties"]["_ontology_context_hash"] == payload["ontology_context"]["context_hash"]
    assert written_rel["properties"]["_ontology_profile"] == "finder-financials"


def test_assess_ontology_context_mismatch_detects_drift() -> None:
    active = compile_ontology_context(_ontology(), workspace_id="acme")

    result = assess_ontology_context_mismatch(
        active,
        ["old-context-hash", active.descriptor.context_hash],
        missing_context_nodes=2,
        scoped_nodes=10,
    )

    assert result["mismatch"] is True
    assert result["active_context_hash"] == active.descriptor.context_hash
    assert "old-context-hash" in result["indexed_context_hashes"]
    assert result["missing_context_nodes"] == 2
    assert result["warning"]


def test_local_engine_query_guardrail_surfaces_mismatch() -> None:
    engine = _LocalEngine(
        ontology=_ontology(),
        graph_store=_MismatchGraphStore(),
        llm=_FakeLLM(),
        workspace_id="acme",
        ontology_profile="finder-financials",
    )
    active = engine._ontology_context_cache.get(
        engine.ontology,
        workspace_id="acme",
        profile="finder-financials",
    )

    result = engine._query_ontology_context_mismatch("neo4j", active)

    assert result["mismatch"] is True
    assert result["active_context_hash"] == active.descriptor.context_hash
    assert result["indexed_context_hashes"] == ["old-context-hash"]
    assert result["scoped_nodes"] == 3


def test_query_ontology_context_mismatch_helper_handles_graph_metadata() -> None:
    active = compile_ontology_context(_ontology(), workspace_id="acme")
    store = _MismatchGraphStore()

    result = query_ontology_context_mismatch(
        store,
        active,
        workspace_id="acme",
        database="neo4j",
    )

    assert result["mismatch"] is True
    assert result["missing_context_nodes"] == 1
    assert result["indexed_context_hashes"] == ["old-context-hash"]
    assert "OPTIONAL MATCH (n:Document)" in store.queries[0]


def test_assess_graph_ontology_context_status_flags_indexed_hash_drift() -> None:
    """Phase 1: when expected_context_hash is set, indexed hashes that differ
    must surface as a structural mismatch reason, not just be reported."""

    result = assess_graph_ontology_context_status(
        database="kgnormal",
        workspace_id="acme",
        indexed_context_hashes=["old-context-hash"],
        expected_context_hash="active-context-hash",
        scoped_nodes=5,
    )

    assert result["mismatch"] is True
    assert "indexed_context_hash_differs_from_active" in result["mismatch_reasons"]
    assert result["expected_context_hash"] == "active-context-hash"
    assert result["indexed_context_hashes"] == ["old-context-hash"]


def test_assess_graph_ontology_context_status_passes_when_hash_matches() -> None:
    result = assess_graph_ontology_context_status(
        database="kgnormal",
        workspace_id="acme",
        indexed_context_hashes=["matching-hash"],
        expected_context_hash="matching-hash",
        scoped_nodes=5,
    )

    assert result["mismatch"] is False
    assert "indexed_context_hash_differs_from_active" not in result["mismatch_reasons"]


def test_assess_graph_ontology_context_status_skips_hash_check_when_unset() -> None:
    """When expected_context_hash is empty (Phase 1 default), the hash drift
    reason must not fire — Phase 1.5 wires the loader that populates it."""

    result = assess_graph_ontology_context_status(
        database="kgnormal",
        workspace_id="acme",
        indexed_context_hashes=["any-hash"],
        expected_context_hash="",
        scoped_nodes=5,
    )

    assert "indexed_context_hash_differs_from_active" not in result["mismatch_reasons"]


def test_build_ontology_context_summary_query_uses_document_scope() -> None:
    base = build_ontology_context_summary_query()
    runtime = build_ontology_context_summary_query(include_runtime_fields=True)

    assert "OPTIONAL MATCH (n:Document)" in base
    assert "raw_ontology_ids" not in base
    assert "OPTIONAL MATCH (n:Document)" in runtime
    assert "raw_ontology_ids" in runtime
    assert "raw_profiles" in runtime
