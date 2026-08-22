#!/usr/bin/env python3
"""Create an isolated, idempotent live fixture for Text2Cypher evaluation."""

from __future__ import annotations

import argparse
import json
import os

from neo4j import GraphDatabase


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bolt-uri", required=True)
    parser.add_argument("--graph-user", required=True)
    password = parser.add_mutually_exclusive_group(required=True)
    password.add_argument("--graph-password", help="Graph password (avoid in shared shells)")
    password.add_argument(
        "--graph-password-env",
        help="Environment variable containing the graph password",
    )
    parser.add_argument("--workspace-id", default="seocho-text2cypher-e2e-fixture")
    args = parser.parse_args()
    graph_password = (
        os.environ.get(args.graph_password_env, "")
        if args.graph_password_env
        else args.graph_password
    )
    if not graph_password:
        raise SystemExit("graph password environment variable is unset or empty")
    driver = GraphDatabase.driver(
        args.bolt_uri, auth=(args.graph_user, graph_password)
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
