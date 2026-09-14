"""Capability-driven read-only schema observations for Neo4j/DozerDB.

Database-wide metadata is operator evidence. A shared database's schema is never
labelled workspace-filtered. Logical workspace shape uses fixed scoped Cypher.
"""

from __future__ import annotations

from typing import Any


CAPABILITIES = (
    "db.schema.nodeTypeProperties",
    "db.schema.relTypeProperties",
    "apoc.meta.nodeTypeProperties",
    "apoc.meta.relTypeProperties",
    "apoc.meta.schema",
)


def _run(session: Any, query: str, **parameters: Any) -> Any:
    from neo4j import Query

    return session.run(Query(query, timeout=30), **parameters)


def discover_capabilities(session: Any) -> dict[str, Any]:
    procedures = [
        dict(r)
        for r in _run(
            session,
            "SHOW PROCEDURES EXECUTABLE BY CURRENT USER YIELD name, mode RETURN name, mode",
        )
    ]
    available = {r["name"]: r["mode"] for r in procedures if r["name"] in CAPABILITIES}
    return {
        "scope": "database_operator",
        "available": available,
        "missing": [name for name in CAPABILITIES if name not in available],
        "administrative_procedures_exposed_to_agent": False,
    }


def discover_database_schema(session: Any, *, sample: int = 1000) -> dict[str, Any]:
    """Operator-only database-wide observations; requires actual DB privileges."""
    if not 1 <= sample <= 10000:
        raise ValueError("Schema sample must be between 1 and 10000")
    result = discover_capabilities(session)
    observations, errors = [], []
    for kind, candidates in [
        ("nodes", ("apoc.meta.nodeTypeProperties", "db.schema.nodeTypeProperties")),
        (
            "relationships",
            ("apoc.meta.relTypeProperties", "db.schema.relTypeProperties"),
        ),
    ]:
        for name in candidates:
            if result["available"].get(name) != "READ":
                continue
            query = "CALL " + name + ("($config)" if name.startswith("apoc.") else "()")
            try:
                records = [
                    dict(r)
                    for r in _run(
                        session,
                        query,
                        **(
                            {"config": {"sample": sample}}
                            if name.startswith("apoc.")
                            else {}
                        ),
                    )
                ]
                observations.append(
                    {
                        "kind": kind,
                        "procedure": name,
                        "rows": records,
                        "sample_requested": sample
                        if name.startswith("apoc.")
                        else None,
                        "completeness": "observed_not_ontology_constraint",
                    }
                )
                break
            except Exception as exc:
                errors.append({"procedure": name, "error_class": type(exc).__name__})
    for name, query in [
        ("indexes", "SHOW INDEXES"),
        ("constraints", "SHOW CONSTRAINTS"),
    ]:
        try:
            observations.append(
                {
                    "kind": name,
                    "rows": [dict(r) for r in _run(session, query)],
                    "source": "declared_database_metadata",
                }
            )
        except Exception as exc:
            errors.append({"operation": name, "error_class": type(exc).__name__})
    return {
        **result,
        "observations": observations,
        "errors": errors,
        "note": "Observed mandatory/type statistics are not FIBO axioms. Metadata may be sampled or privilege-limited.",
    }


def discover_workspace_shape(
    session: Any, workspace_id: str, *, limit: int = 500
) -> dict[str, Any]:
    """Sample logical kinds and directed relation patterns within a workspace.

    This supports SEOCHO's generic physical label projection using n.kind and
    r.relation, with physical labels/types as fallback. No plugin installation.
    """
    if not workspace_id or not 1 <= limit <= 10000:
        raise ValueError("A workspace and bounded sample size are required")
    nodes = [
        dict(r)
        for r in _run(
            session,
            "MATCH (n) WHERE n._workspace_id=$workspace_id "
            "WITH n LIMIT $limit "
            "RETURN labels(n) AS physical_labels,n.kind AS logical_kind,keys(n) AS properties",
            workspace_id=workspace_id,
            limit=limit + 1,
        )
    ]
    relationships = [
        dict(r)
        for r in _run(
            session,
            "MATCH (a)-[r]->(b) WHERE a._workspace_id=$workspace_id "
            "AND b._workspace_id=$workspace_id AND r._workspace_id=$workspace_id "
            "WITH a,r,b LIMIT $limit "
            "RETURN labels(a) AS source_labels,a.kind AS source_kind,type(r) AS physical_type,"
            "r.relation AS logical_relation,labels(b) AS target_labels,b.kind AS target_kind",
            workspace_id=workspace_id,
            limit=limit + 1,
        )
    ]
    return {
        "scope": "workspace",
        "workspace_id": workspace_id,
        "source": "fixed_scoped_cypher",
        "nodes": nodes[:limit],
        "relationships": relationships[:limit],
        "truncated": len(nodes) > limit or len(relationships) > limit,
        "sampling": "bounded observed rows; no guaranteed complete inventory",
        "ontology_semantics": "not_inferred_from_observed_schema",
    }
