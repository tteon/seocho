"""Regression checks for workspace-scoped indexing and session fallback reads."""

from typing import Any

from seocho.index.pipeline import IndexingPipeline
from seocho.models import Memory
from seocho.ontology import NodeDef, Ontology, P
from seocho.session import Session


def test_relationship_census_requires_owned_endpoints_and_relationship() -> None:
    class Store:
        def write(self, nodes: list, relationships: list, **kwargs: Any) -> dict:
            return {"nodes_created": len(nodes), "relationships_created": 3}

        def query(self, cypher: str, **kwargs: Any) -> list[dict]:
            assert kwargs["workspace_id"] == "tenant-a"
            assert kwargs["enforce_workspace_filter"] is True
            assert kwargs["params"]["workspace_id"] == "tenant-a"
            assert kwargs["params"]["sid"] == "shared-source"
            assert "r._source_id=$sid" in cypher
            for entity in ("a", "b", "r"):
                assert f"{entity}._workspace_id=$workspace_id" in cypher
            return [{"c": 1}]

    pipeline = IndexingPipeline(
        ontology=Ontology(
            name="scoped", nodes={"Company": NodeDef(properties={"name": P(str)})},
            relationships={},
        ),
        graph_store=Store(), llm=None, workspace_id="tenant-a",
    )
    result = pipeline.index_graph(
        {"nodes": [{"id": "acme", "label": "Company", "properties": {"name": "Acme"}}],
         "relationships": []},
        source_id="shared-source",
    )
    assert result.relationship_census["written_to_store"] == 1


def test_session_fallback_reads_only_the_session_workspace(monkeypatch: Any) -> None:
    class Pipeline:
        def add(self, *args: Any, **kwargs: Any) -> Memory:
            return Memory(
                memory_id="shared-source", workspace_id="tenant-a", content="Acme",
                metadata={}, status="active", database="neo4j", category="general",
            )

    class Store:
        def query(self, cypher: str, **kwargs: Any) -> list[dict]:
            assert kwargs["workspace_id"] == "tenant-a"
            assert kwargs["enforce_workspace_filter"] is True
            assert kwargs["params"] == {"sid": "shared-source", "workspace_id": "tenant-a"}
            assert "n._workspace_id = $workspace_id" in cypher
            return [{"label": "Company", "props": {"name": "Acme"}}]

    session = Session(
        ontology=Ontology(name="scoped", nodes={}, relationships={}),
        graph_store=Store(), llm=None, workspace_id="tenant-a",
    )
    monkeypatch.setattr(session, "_get_pipeline_engine", lambda: Pipeline())
    result = session._add_via_pipeline("Acme", "neo4j", "general", None)
    assert result["extracted_nodes"] == [{"label": "Company", "properties": {"name": "Acme"}}]
    session.close()
