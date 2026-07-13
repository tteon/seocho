"""Neo4j / DozerDB schema materialization helpers."""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Any, Iterable, Mapping, Optional

from .records import ConnectorRecord, stable_record_id


class ConnectorAPIError(RuntimeError):
    """Raised when Neo4j connector setup fails."""


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def records_from_schema_rows(
    *,
    node_rows: Iterable[Mapping[str, Any]],
    relationship_rows: Iterable[Mapping[str, Any]],
    database: str = "",
    category: str = "neo4j",
) -> list[ConnectorRecord]:
    """Convert Neo4j schema procedure rows into one SEOCHO record."""

    node_properties: dict[str, list[dict[str, Any]]] = defaultdict(list)
    relationship_properties: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in node_rows:
        node_type = str(row.get("nodeType") or row.get("label") or "")
        property_name = str(row.get("propertyName") or "")
        if not node_type or not property_name:
            continue
        node_properties[node_type].append(
            {
                "property_name": property_name,
                "property_types": [str(item) for item in _as_list(row.get("propertyTypes"))],
                "mandatory": bool(row.get("mandatory")),
            }
        )

    for row in relationship_rows:
        rel_type = str(row.get("relType") or row.get("relationshipType") or "")
        property_name = str(row.get("propertyName") or "")
        if not rel_type or not property_name:
            continue
        relationship_properties[rel_type].append(
            {
                "property_name": property_name,
                "property_types": [str(item) for item in _as_list(row.get("propertyTypes"))],
                "mandatory": bool(row.get("mandatory")),
            }
        )

    title = f"Neo4j schema {database}".strip()
    lines = [f"# {title}", "", "## Node Types"]
    if node_properties:
        for node_type, fields in sorted(node_properties.items()):
            lines.append(f"- {node_type}")
            for field in sorted(fields, key=lambda item: str(item["property_name"])):
                types = ", ".join(field["property_types"]) or "unknown"
                required = " required" if field["mandatory"] else ""
                lines.append(f"  - {field['property_name']}: {types}{required}")
    else:
        lines.append("- none observed")

    lines += ["", "## Relationship Types"]
    if relationship_properties:
        for rel_type, fields in sorted(relationship_properties.items()):
            lines.append(f"- {rel_type}")
            for field in sorted(fields, key=lambda item: str(item["property_name"])):
                types = ", ".join(field["property_types"]) or "unknown"
                required = " required" if field["mandatory"] else ""
                lines.append(f"  - {field['property_name']}: {types}{required}")
    else:
        lines.append("- none observed")

    content = "\n".join(lines)
    external_id = f"neo4j://{database or 'default'}/schema"
    return [
        ConnectorRecord(
            id=stable_record_id("neo4j", external_id, content),
            content=content,
            provider="neo4j",
            source_kind="neo4j_schema",
            category=category,
            title=title,
            metadata={
                "external_id": external_id,
                "database": database,
                "node_types": sorted(node_properties),
                "relationship_types": sorted(relationship_properties),
                "node_properties": dict(node_properties),
                "relationship_properties": dict(relationship_properties),
            },
        )
    ]


def fetch_schema_records(
    *,
    uri: Optional[str] = None,
    uri_env: str = "NEO4J_URI",
    user_env: str = "NEO4J_USER",
    password_env: str = "NEO4J_PASSWORD",
    database: str = "",
    category: str = "neo4j",
) -> list[ConnectorRecord]:
    """Fetch Neo4j / DozerDB schema metadata using the optional Neo4j driver."""

    resolved_uri = uri or os.environ.get(uri_env) or "bolt://localhost:7687"
    user = os.environ.get(user_env, "")
    password = os.environ.get(password_env, "")
    auth = (user, password) if user or password else None
    try:
        from neo4j import GraphDatabase
    except Exception as exc:  # pragma: no cover - exercised only without optional extra
        raise ConnectorAPIError(
            "Neo4j connector requires the Neo4j driver. Install with: pip install 'seocho[local]'"
        ) from exc

    node_query = """
        CALL db.schema.nodeTypeProperties()
        YIELD nodeType, propertyName, propertyTypes, mandatory
        RETURN nodeType, propertyName, propertyTypes, mandatory
        ORDER BY nodeType, propertyName
    """
    rel_query = """
        CALL db.schema.relTypeProperties()
        YIELD relType, propertyName, propertyTypes, mandatory
        RETURN relType, propertyName, propertyTypes, mandatory
        ORDER BY relType, propertyName
    """

    driver = GraphDatabase.driver(resolved_uri, auth=auth)
    try:
        with driver.session(database=database or None) as session:
            node_rows = [dict(record) for record in session.run(node_query)]
            relationship_rows = [dict(record) for record in session.run(rel_query)]
    finally:
        driver.close()
    return records_from_schema_rows(
        node_rows=node_rows,
        relationship_rows=relationship_rows,
        database=database,
        category=category,
    )


__all__ = [
    "ConnectorAPIError",
    "fetch_schema_records",
    "records_from_schema_rows",
]
