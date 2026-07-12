#!/usr/bin/env python3
"""Live MARA Text2Cypher fallback against the exchange-memory graph."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from neo4j import GraphDatabase

from seocho.query.text2cypher import generate_validated_cypher
from seocho.query.workload_compiler import Text2CypherFallbackPolicy
from seocho.store.llm import MaraBackend


async def run(args: argparse.Namespace) -> dict:
    driver = GraphDatabase.driver(args.bolt_uri, auth=("neo4j", args.graph_password))
    with driver.session() as session:
        seed = session.run(
            "MATCH (i:ExchangeIntent)-[:HAS_EVENT]->(e:ExchangeMemoryEvent) "
            "RETURN i.workspace AS workspace_id,i.id AS intent_id,count(e) AS events "
            "ORDER BY events DESC LIMIT 1"
        ).single()
    if seed is None:
        raise RuntimeError("exchange memory graph is empty")
    params = {
        "workspace_id": seed["workspace_id"],
        "intent_id": seed["intent_id"],
        "limit": 50,
    }
    policy = Text2CypherFallbackPolicy(
        allowed_labels=("ExchangeIntent", "ExchangeMemoryEvent"),
        allowed_relationships=("HAS_EVENT", "NEXT"),
        allowed_properties=("id", "workspace", "step", "sequence", "actor", "recipient", "provenance"),
        workspace_property="workspace",
        required_parameters=("workspace_id", "intent_id"),
        max_graph_hops=2,
        max_result_rows=50,
        max_repair_attempts=1,
    )

    async def explain(cypher, values):
        with driver.session() as session:
            session.run("EXPLAIN " + cypher, **values).consume()

    result = await generate_validated_cypher(
        question="List the ordered memory steps recorded for this exchange transaction.",
        schema={
            "labels": policy.allowed_labels,
            "relationships": policy.allowed_relationships,
            "properties": policy.allowed_properties,
            "tenant_scope": "workspace:$workspace_id",
        },
        params=params,
        policy=policy,
        backend=MaraBackend(model=args.model),
        model=args.model,
        explain=explain,
    )
    with driver.session() as session:
        records = session.run(result.cypher, **result.params).data()
    driver.close()
    report = {
        "schema_version": "seocho.okx-text2cypher-live.v1",
        "model": args.model,
        "tier": "validated_text2cypher",
        "attempts": result.attempts,
        "explained": result.explained,
        "query_hash": hashlib.sha256(result.cypher.encode()).hexdigest()[:16],
        "result_rows": len(records),
        "workspace_scoped": "$workspace_id" in result.cypher,
        "parameterized_limit": "LIMIT $limit" in result.cypher,
        "passed": bool(records)
        and any(any(value not in (None, [], {}) for value in record.values()) for record in records)
        and result.explained,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bolt-uri", default="bolt://127.0.0.1:7687")
    parser.add_argument("--graph-password", required=True)
    parser.add_argument("--model", default="gpt-oss-120b")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
