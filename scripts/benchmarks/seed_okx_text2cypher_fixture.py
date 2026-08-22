#!/usr/bin/env python3
"""Create an isolated, idempotent live fixture for Text2Cypher evaluation."""

from __future__ import annotations

import argparse
import json

from neo4j import GraphDatabase


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bolt-uri", required=True)
    parser.add_argument("--graph-user", required=True)
    parser.add_argument("--graph-password", required=True)
    parser.add_argument("--workspace-id", default="seocho-text2cypher-e2e-fixture")
    args = parser.parse_args()
    driver = GraphDatabase.driver(
        args.bolt_uri, auth=(args.graph_user, args.graph_password)
    )
    try:
        with driver.session() as session:
            record = session.run(
                "MERGE (i:ExchangeIntent {id: $intent_id, workspace: $workspace_id}) "
                "WITH i UNWIND range(1, 3) AS sequence "
                "MERGE (e:ExchangeMemoryEvent {id: $workspace_id + ':event:' + toString(sequence), workspace: $workspace_id}) "
                "SET e.sequence = sequence, e.step = 'fixture_step_' + toString(sequence), e.provenance = 'seocho_e2e_fixture' "
                "MERGE (i)-[:HAS_EVENT]->(e) "
                "RETURN count(e) AS event_count",
                intent_id="fixture-intent",
                workspace_id=args.workspace_id,
            ).single()
        print(
            json.dumps(
                {
                    "workspace_id": args.workspace_id,
                    "event_count": int(record["event_count"]),
                    "fixture": "okx_text2cypher_e2e.v1",
                },
                sort_keys=True,
            )
        )
    finally:
        driver.close()


if __name__ == "__main__":
    main()
