"""PostgreSQL schema materialization for SEOCHO connector records."""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Any, Iterable, Mapping, Optional

from .records import ConnectorRecord, stable_record_id


class ConnectorAPIError(RuntimeError):
    """Raised when PostgreSQL connector setup fails."""


def records_from_schema_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    database: str = "",
    category: str = "postgres",
) -> list[ConnectorRecord]:
    """Convert ``information_schema.columns`` rows into dataset records."""

    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        schema = str(row.get("table_schema") or "")
        table = str(row.get("table_name") or "")
        if not schema or not table:
            continue
        grouped[(schema, table)].append(row)

    records: list[ConnectorRecord] = []
    for (schema, table), cols in sorted(grouped.items()):
        columns = sorted(cols, key=lambda item: int(item.get("ordinal_position") or 0))
        qualified = ".".join(part for part in (database, schema, table) if part)
        lines = [f"# PostgreSQL table {qualified or f'{schema}.{table}'}", "", "## Columns"]
        field_meta: list[dict[str, Any]] = []
        for col in columns:
            name = str(col.get("column_name") or "")
            dtype = str(col.get("data_type") or col.get("udt_name") or "unknown")
            nullable = str(col.get("is_nullable") or "")
            lines.append(f"- {name}: {dtype}" + (f" nullable={nullable}" if nullable else ""))
            field_meta.append(
                {
                    "name": name,
                    "data_type": dtype,
                    "ordinal_position": col.get("ordinal_position"),
                    "is_nullable": nullable,
                }
            )
        content = "\n".join(lines)
        external_id = f"postgres://{qualified or f'{schema}.{table}'}"
        records.append(
            ConnectorRecord(
                id=stable_record_id("postgres", external_id, content),
                content=content,
                provider="postgres",
                source_kind="postgres_table_schema",
                category=category,
                title=qualified or f"{schema}.{table}",
                metadata={
                    "external_id": external_id,
                    "database": database,
                    "table_schema": schema,
                    "table_name": table,
                    "qualified_name": qualified,
                    "field_count": len(field_meta),
                    "fields": field_meta,
                },
            )
        )
    return records


def fetch_schema_records(
    *,
    dsn: Optional[str] = None,
    dsn_env: str = "DATABASE_URL",
    schemas: Optional[list[str]] = None,
    database: str = "",
    category: str = "postgres",
) -> list[ConnectorRecord]:
    """Fetch PostgreSQL schema metadata using optional ``psycopg``.

    The first slice is schema-only by default. It does not sample raw rows, so
    users can try the connector with a low-privilege metadata reader.
    """

    resolved = dsn or os.environ.get(dsn_env)
    if not resolved:
        raise ConnectorAPIError(f"PostgreSQL DSN not found. Export {dsn_env}.")
    try:
        import psycopg
        from psycopg.rows import dict_row
    except Exception as exc:  # pragma: no cover - exercised only without optional extra
        raise ConnectorAPIError(
            "PostgreSQL connector requires psycopg. Install with: pip install 'seocho[postgres]'"
        ) from exc

    query = """
        select table_schema, table_name, column_name, ordinal_position,
               data_type, udt_name, is_nullable
        from information_schema.columns
        where table_schema not in ('pg_catalog', 'information_schema')
    """
    params: list[Any] = []
    if schemas:
        query += " and table_schema = any(%s)"
        params.append(schemas)
    query += " order by table_schema, table_name, ordinal_position"

    with psycopg.connect(resolved, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = list(cur.fetchall())
    return records_from_schema_rows(rows, database=database, category=category)


__all__ = [
    "ConnectorAPIError",
    "fetch_schema_records",
    "records_from_schema_rows",
]
