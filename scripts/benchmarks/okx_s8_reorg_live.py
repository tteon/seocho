#!/usr/bin/env python3
"""Live S8 append-only reorg compensation and graph rebuild parity."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from neo4j import GraphDatabase

from seocho.memory import PostgreSQLMemoryRepository


def run(args: argparse.Namespace) -> dict:
    workspace = f"s8-reorg-{uuid.uuid4().hex[:12]}"
    memory_id = "btc-settlement-001"
    repo = PostgreSQLMemoryRepository.connect(args.dsn)
    confirmed = repo.commit_revision(
        workspace_id=workspace, memory_id=memory_id, event_type="confirmed",
        occurred_at="2026-07-12T00:00:00+00:00", provenance_id="block:900001:a",
        payload={"state": "confirmed", "block_ref": "block-a", "status": "canonical"},
        idempotency_key="s8-confirmed",
    )
    repo.commit_revision(
        workspace_id=workspace, memory_id=memory_id, event_type="orphaned",
        occurred_at="2026-07-12T00:01:00+00:00", provenance_id="reorg:block-a:block-b",
        payload={"state": "reversed", "block_ref": "block-a", "status": "orphaned"},
        idempotency_key="s8-orphan", canonical=False,
        allowed_previous_event_types=("confirmed",),
    )
    replacement = repo.commit_revision(
        workspace_id=workspace, memory_id=memory_id, event_type="confirmed_replacement",
        occurred_at="2026-07-12T00:02:00+00:00", provenance_id="block:900001:b",
        payload={"state": "confirmed", "block_ref": "block-b", "status": "canonical"},
        idempotency_key="s8-replacement",
        allowed_previous_event_types=("orphaned",),
    )
    history = tuple(reversed(repo.read_history(workspace_id=workspace, memory_id=memory_id)))
    rows = [
        {
            "id": f"{workspace}:{item.revision}", "workspace": workspace,
            "revision": item.revision, "sequence": item.sequence,
            "event_type": item.event_type, "state": item.payload["state"],
            "block_ref": item.payload["block_ref"], "status": item.payload["status"],
            "canonical": item.canonical, "provenance": item.provenance_id,
        }
        for item in history
    ]
    driver = GraphDatabase.driver(args.bolt_uri, auth=("neo4j", args.graph_password))

    def rebuild() -> dict:
        with driver.session() as session:
            session.run("MATCH (n:S8Revision {workspace:$workspace}) DETACH DELETE n", workspace=workspace).consume()
            session.run(
                "UNWIND $rows AS row CREATE (n:S8Revision {id:row.id}) SET n += row",
                rows=rows,
            ).consume()
            value = session.run(
                "MATCH (n:S8Revision {workspace:$workspace}) "
                "RETURN count(n) AS revisions,sum(CASE WHEN n.canonical THEN 1 ELSE 0 END) AS canonical,"
                "collect(n.status) AS statuses",
                workspace=workspace,
            ).single()
            return {"revisions": value["revisions"], "canonical": value["canonical"], "statuses": sorted(value["statuses"])}

    first = rebuild()
    second = rebuild()
    driver.close()
    historical = repo.read_revision(
        workspace_id=workspace, memory_id=memory_id, at_sequence=confirmed.causal_token.sequence
    )
    current = repo.read_revision(workspace_id=workspace, memory_id=memory_id)
    passed = (
        first == second and first["revisions"] == 3 and first["canonical"] == 1
        and historical.payload["block_ref"] == "block-a"
        and current.payload["block_ref"] == "block-b"
        and current.sequence == replacement.causal_token.sequence
        and any(item.event_type == "orphaned" for item in history)
    )
    return {
        "schema_version": "seocho.s8-reorg-live.v1", "source": "live-postgresql-dozerdb",
        "history": [{"revision": item.revision, "event_type": item.event_type, "canonical": item.canonical} for item in history],
        "historical_answer": {"state": historical.payload["state"], "block_ref": historical.payload["block_ref"], "sequence": historical.sequence},
        "current_answer": {"state": current.payload["state"], "block_ref": current.payload["block_ref"], "sequence": current.sequence},
        "first_projection": first, "rebuilt_projection": second, "passed": passed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--bolt-uri", default="bolt://127.0.0.1:7687")
    parser.add_argument("--graph-password", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
