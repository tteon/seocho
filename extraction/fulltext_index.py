"""
Fulltext index discovery/bootstrap helpers for DozerDB/Neo4j-compatible backends.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_valid_identifier(value: str) -> bool:
    return bool(_IDENT_RE.match(value))


def validate_identifiers(values: Sequence[str], field_name: str) -> List[str]:
    cleaned: List[str] = []
    for value in values:
        ident = str(value).strip()
        if not ident:
            continue
        if not is_valid_identifier(ident):
            raise ValueError(
                f"Invalid identifier '{ident}' in '{field_name}'. Use letters, digits, underscore; must not start with digit."
            )
        cleaned.append(ident)
    if not cleaned:
        raise ValueError(f"'{field_name}' must contain at least one valid identifier")
    return cleaned


def _parse_rows(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, str) and raw.startswith("Error"):
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    if isinstance(parsed, list):
        return parsed
    return []


class FulltextIndexManager:
    """Inspect and ensure fulltext indexes."""

    def __init__(self, connector: Any):
        self.connector = connector

    def list_fulltext_indexes(self, database: str) -> List[Dict[str, Any]]:
        query_candidates = [
            """
            SHOW FULLTEXT INDEXES
            YIELD name, state, entityType, labelsOrTypes, properties
            RETURN name, state, entityType, labelsOrTypes, properties
            """,
            """
            SHOW INDEXES
            YIELD name, type, state, entityType, labelsOrTypes, properties
            WHERE type = 'FULLTEXT'
            RETURN name, state, entityType, labelsOrTypes, properties
            """,
        ]
        for query in query_candidates:
            rows = _parse_rows(self.connector.run_cypher(query=query, database=database, params=None))
            if rows:
                return rows
        return []

    def ensure_index(
        self,
        database: str,
        index_name: str,
        labels: Sequence[str],
        properties: Sequence[str],
        create_if_missing: bool = True,
    ) -> Dict[str, Any]:
        index_name = validate_identifiers([index_name], "index_name")[0]
        safe_labels = validate_identifiers(labels, "labels")
        safe_props = validate_identifiers(properties, "properties")

        existing = self.list_fulltext_indexes(database)
        matched = next((row for row in existing if row.get("name") == index_name), None)
        if matched:
            return {
                "database": database,
                "index_name": index_name,
                "exists": True,
                "created": False,
                "state": str(matched.get("state", "")),
                "labels": safe_labels,
                "properties": safe_props,
                "message": "Index already exists.",
            }

        if not create_if_missing:
            return {
                "database": database,
                "index_name": index_name,
                "exists": False,
                "created": False,
                "state": None,
                "labels": safe_labels,
                "properties": safe_props,
                "message": "Index not found.",
            }

        create_mode, error = self._create_index(
            database=database,
            index_name=index_name,
            labels=safe_labels,
            properties=safe_props,
        )
        refreshed = self.list_fulltext_indexes(database)
        matched_after = next((row for row in refreshed if row.get("name") == index_name), None)
        created = matched_after is not None

        if created:
            return {
                "database": database,
                "index_name": index_name,
                "exists": True,
                "created": True,
                "state": str(matched_after.get("state", "")),
                "labels": safe_labels,
                "properties": safe_props,
                "message": f"Index created via {create_mode}.",
            }

        message = f"Index creation attempted via {create_mode} but not visible."
        if error:
            message = f"{message} Last error: {error}"
        return {
            "database": database,
            "index_name": index_name,
            "exists": False,
            "created": False,
            "state": None,
            "labels": safe_labels,
            "properties": safe_props,
            "message": message,
        }

    def _create_index(
        self,
        database: str,
        index_name: str,
        labels: Sequence[str],
        properties: Sequence[str],
    ) -> Tuple[str, Optional[str]]:
        label_expr = "|".join(labels)
        prop_expr = ", ".join([f"n.{prop}" for prop in properties])

        create_query = (
            f"CREATE FULLTEXT INDEX {index_name} IF NOT EXISTS "
            f"FOR (n:{label_expr}) ON EACH [{prop_expr}]"
        )
        raw = self.connector.run_cypher(query=create_query, database=database, params=None)
        if not (isinstance(raw, str) and raw.startswith("Error")):
            return "cypher_ddl", None

        fallback = self.connector.run_cypher(
            query="CALL db.index.fulltext.createNodeIndex($name, $labels, $properties)",
            database=database,
            params={
                "name": index_name,
                "labels": list(labels),
                "properties": list(properties),
            },
        )
        if isinstance(fallback, str) and fallback.startswith("Error"):
            return "procedure_fallback", fallback
        return "procedure_fallback", None

