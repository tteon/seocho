#!/usr/bin/env python3
"""Live S6 physical fan-out degradation and S7 etcd policy drift."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import time
import urllib.request
from pathlib import Path

from neo4j import GraphDatabase


def _post(base: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        return json.loads(response.read())


def _b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


async def _target(name: str, uri: str, password: str, timeout: float) -> dict:
    def query() -> int:
        driver = GraphDatabase.driver(
            uri,
            auth=("neo4j", password),
            connection_timeout=timeout,
            max_transaction_retry_time=0,
        )
        try:
            with driver.session() as session:
                return int(session.run("RETURN 1 AS available").single()["available"])
        finally:
            driver.close()

    started = time.perf_counter()
    try:
        value = await asyncio.wait_for(asyncio.to_thread(query), timeout=timeout)
        return {"target": name, "available": value == 1, "error": "", "elapsed_ms": round((time.perf_counter()-started)*1000, 3)}
    except Exception as exc:
        return {"target": name, "available": False, "error": type(exc).__name__, "elapsed_ms": round((time.perf_counter()-started)*1000, 3)}


async def run(args: argparse.Namespace) -> dict:
    started = time.perf_counter()
    targets = await asyncio.gather(
        _target("user-hot", args.primary, args.password, args.timeout),
        _target("transaction-history", args.secondary, args.password, args.timeout),
        _target("settlement-audit", args.unavailable, args.password, args.timeout),
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    healthy = [target["target"] for target in targets if target["available"]]
    missing = [target["target"] for target in targets if not target["available"]]
    s6_passed = len(healthy) == 2 and missing == ["settlement-audit"] and elapsed_ms < args.timeout * 1500

    key = "/seocho/scenarios/s7/active-policy"
    for version in ("3.0.0", "4.0.0"):
        _post(args.etcd, "/v3/kv/put", {"key": _b64(key), "value": _b64(json.dumps({"policy_version": version}))})
        if version == "3.0.0":
            before = version
    response = _post(args.etcd, "/v3/kv/range", {"key": _b64(key)})
    active = json.loads(base64.b64decode(response["kvs"][0]["value"]))["policy_version"]
    raw = {"state": "filled", "policy_version": active, "raw_account_id": "secret", "internal_note": "secret"}
    visible = {key: value for key, value in raw.items() if key in {"state", "policy_version"}}
    rendered = json.dumps(visible)
    s7_passed = before == "3.0.0" and active == "4.0.0" and "secret" not in rendered
    _post(args.etcd, "/v3/kv/deleterange", {"key": _b64(key)})
    return {
        "schema_version": "seocho.s6-s7-live.v1",
        "s6": {"targets": targets, "healthy_targets": healthy, "missing_targets": missing, "support_status": "partial", "total_ms": round(elapsed_ms, 3), "passed": s6_passed},
        "s7": {"previous_policy": before, "active_policy": active, "visible_fields": sorted(visible), "leakage": "secret" in rendered, "passed": s7_passed},
        "passed": s6_passed and s7_passed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary", default="bolt://127.0.0.1:7687")
    parser.add_argument("--secondary", default="bolt://127.0.0.1:7797")
    parser.add_argument("--unavailable", default="bolt://127.0.0.1:57999")
    parser.add_argument("--password", required=True)
    parser.add_argument("--etcd", default="http://127.0.0.1:52379")
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
