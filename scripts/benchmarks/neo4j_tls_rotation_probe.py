#!/usr/bin/env python3
"""Capability-gated Neo4j Enterprise TLS handshake and reload probe."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from neo4j import GraphDatabase


def run(args: argparse.Namespace) -> dict:
    started = time.perf_counter()
    driver = GraphDatabase.driver(args.uri, auth=("neo4j", args.password))
    try:
        with driver.session() as session:
            before = session.run("RETURN 1 AS ok").single()["ok"] == 1
            settings = session.run(
                "SHOW SETTINGS YIELD name,value "
                "WHERE name='dbms.security.tls_reload_enabled' RETURN name,value"
            ).data()
            procedures = session.run(
                "SHOW PROCEDURES YIELD name WHERE name='dbms.security.reloadTLS' RETURN name"
            ).data()
            supported = bool(settings and str(settings[0]["value"]).lower() == "true" and procedures)
            reloaded = False
            if args.reload and supported:
                session.run("CALL dbms.security.reloadTLS()").consume()
                reloaded = True
        driver.close()
        new_driver = GraphDatabase.driver(args.uri, auth=("neo4j", args.password))
        try:
            with new_driver.session() as session:
                after = session.run("RETURN 1 AS ok").single()["ok"] == 1
        finally:
            new_driver.close()
    finally:
        driver.close()
    report = {
        "schema_version": "seocho.neo4j-tls-rotation-probe.v1",
        "uri_scheme": args.uri.split(":", 1)[0],
        "encrypted_scheme": args.uri.startswith(("bolt+s:", "bolt+ssc:", "neo4j+s:", "neo4j+ssc:")),
        "handshake_before": before,
        "dynamic_reload_supported": supported,
        "reload_requested": args.reload,
        "reload_executed": reloaded,
        "handshake_after": after,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "passed": before and after and supported and (not args.reload or reloaded),
        "status": "passed" if before and after and supported and (not args.reload or reloaded) else "capability_gated",
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", default="bolt+ssc://127.0.0.1:57677")
    parser.add_argument("--password", required=True)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--require-supported", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(1 if args.require_supported and not report["passed"] else 0)


if __name__ == "__main__":
    main()
