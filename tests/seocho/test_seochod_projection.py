from __future__ import annotations

from typing import Any

from seocho.store.graph import Neo4jGraphStore


def test_write_routes_to_rust_projector_when_socket_is_configured(
    monkeypatch: Any,
) -> None:
    captured: dict[str, Any] = {}

    class FakeDriver:
        def session(self, **_: Any) -> Any:
            raise AssertionError("Python Bolt writer must not be used")

    class FakeProjector:
        def __init__(self, socket_path: str) -> None:
            captured["socket"] = socket_path

        def project(self, nodes: Any, relationships: Any, **kwargs: Any) -> dict[str, Any]:
            captured.update(nodes=nodes, relationships=relationships, **kwargs)
            return {"nodes_created": 1, "relationships_created": 1, "errors": [], "merge_conflicts": [], "driver": "rust-neo4j"}

    monkeypatch.setenv("SEOCHO_RUST_PROJECTOR_SOCKET", "/tmp/seochod.sock")
    monkeypatch.setattr("seocho.dataplane.seochod.SeochodProjectionClient", FakeProjector)
    store = object.__new__(Neo4jGraphStore)
    store._driver = FakeDriver()
    store._schema_cache = {}
    store._schema_cache_ts = {}
    store._index_stats_cache = {}
    store._index_stats_cache_ts = {}

    result = store.write(
        [{"id": "acme", "label": "Company", "properties": {"name": "Acme"}}],
        [{"source": "acme", "target": "cloud", "type": "OFFERS", "properties": {}}],
        workspace_id="workspace-a", source_id="source-a",
    )

    assert result["driver"] == "rust-neo4j"
    assert captured["socket"] == "/tmp/seochod.sock"
    assert captured["workspace_id"] == "workspace-a"
    assert captured["source_id"] == "source-a"
    assert result["governance"]["mode"] == "direct"
    assert not result["governance"]["canonical_claim_allowed"]


def test_rust_projection_uses_the_same_business_identity_as_python_writer(
    monkeypatch: Any,
) -> None:
    captured: dict[str, Any] = {}

    class FakeProjector:
        def __init__(self, _: str) -> None:
            pass

        def project(self, nodes: Any, relationships: Any, **_: Any) -> dict[str, Any]:
            captured["nodes"] = nodes
            captured["relationships"] = relationships
            return {"nodes_created": 2, "relationships_created": 1, "errors": []}

    monkeypatch.setenv("SEOCHO_RUST_PROJECTOR_SOCKET", "/tmp/seochod.sock")
    monkeypatch.setattr("seocho.dataplane.seochod.SeochodProjectionClient", FakeProjector)
    store = object.__new__(Neo4jGraphStore)
    store._schema_cache, store._schema_cache_ts = {}, {}
    store._index_stats_cache, store._index_stats_cache_ts = {}, {}

    store.write(
        [
            {"id": "company-local-1", "label": "Company", "properties": {"name": "Beta Industries"}},
            {"id": "person-local-1", "label": "Person", "properties": {"name": "Jane"}},
        ],
        [{"source": "person-local-1", "target": "company-local-1", "type": "WORKS_AT", "properties": {}}],
        workspace_id="workspace-a",
    )

    assert captured["nodes"][0]["id"] == "Beta Industries"
    assert captured["nodes"][0]["properties"]["id"] == "Beta Industries"
    assert captured["relationships"][0]["source"] == "Jane"
    assert captured["relationships"][0]["target"] == "Beta Industries"
