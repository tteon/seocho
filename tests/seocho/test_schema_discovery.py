"""Read-only contract fixtures; actual DozerDB compatibility needs a live gate."""

from __future__ import annotations

from seocho.connectors.schema_discovery import (
    discover_database_schema,
    discover_workspace_shape,
)


class Session:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def run(self, query, **parameters):
        text = str(query)
        assert query.timeout == 30
        self.calls.append((text, parameters))
        if text.startswith("SHOW PROCEDURES"):
            return [
                {"name": "apoc.meta.nodeTypeProperties", "mode": "WRITE"},
                {"name": "db.schema.nodeTypeProperties", "mode": "READ"},
                {"name": "apoc.periodic.commit", "mode": "WRITE"},
            ]
        if text.startswith("CALL"):
            return [{"nodeLabels": ["Generic"], "propertyName": "kind"}]
        return []


def test_discovery_never_calls_write_or_admin_procedures() -> None:
    session = Session()
    result = discover_database_schema(session)
    assert result["scope"] == "database_operator"
    calls = [q for q, _ in session.calls if q.startswith("CALL")]
    assert calls == ["CALL db.schema.nodeTypeProperties()"]
    assert "apoc.periodic.commit" not in result["available"]


def test_workspace_queries_are_fixed_and_all_endpoints_scoped() -> None:
    session = Session()
    workspace = "malicious' RETURN secret //"
    result = discover_workspace_shape(session, workspace)
    assert result["workspace_id"] == workspace and result["scope"] == "workspace"
    for query, parameters in session.calls:
        assert workspace not in query and parameters["workspace_id"] == workspace
    relationships = session.calls[1][0]
    assert all(
        x + "._workspace_id=$workspace_id" in relationships for x in ("a", "b", "r")
    )
