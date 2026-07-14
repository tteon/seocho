#!/usr/bin/env python3
"""Run and aggregate SEOCHO's live OKX AI-infrastructure release gates."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _run(command: list[str]) -> tuple[bool, str]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    detail = completed.stderr.strip() or completed.stdout.strip()
    return completed.returncode == 0, detail[-1000:]


def _healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
            return 200 <= response.status < 300
    except Exception:
        return False


def evaluate(vertical: dict[str, Any], chaos: dict[str, Any], telemetry: dict[str, bool]) -> dict[str, bool]:
    llm = vertical.get("llm", {})
    return {
        "public_chain_memory": vertical.get("memory", {}).get("projection_current") is True,
        "query_and_guardrail": vertical.get("query", {}).get("plans", 0) > 0
        and vertical.get("guardrail", {}).get("raw_address_in_cases") is False,
        "answer_generation": llm.get("error_count") == 0
        and llm.get("disposition_accuracy") == 1.0
        and llm.get("provenance_coverage") == 1.0
        and llm.get("leakage_cases") == 0,
        "distributed_failover": chaos.get("passed") is True,
        "observability_backends": all(telemetry.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-oss-120b")
    parser.add_argument("--events", type=int, default=300)
    parser.add_argument("--dsn", default=os.getenv("SEOCHO_MEMORY_DSN", ""))
    parser.add_argument("--graph-password", default=os.getenv("SEOCHO_GRAPH_PASSWORD", ""))
    parser.add_argument("--bolt-uri", default="bolt://127.0.0.1:7687")
    parser.add_argument("--etcd", default="http://127.0.0.1:52379")
    parser.add_argument("--prometheus", default="http://127.0.0.1:59090")
    parser.add_argument("--tempo", default="http://127.0.0.1:53200")
    parser.add_argument("--grafana", default="http://127.0.0.1:53000")
    args = parser.parse_args()
    if not args.dsn or not args.graph_password:
        parser.error("SEOCHO_MEMORY_DSN and SEOCHO_GRAPH_PASSWORD are required")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    vertical_path = args.output_dir / "public-chain-answer.json"
    chaos_path = args.output_dir / "projector-failover.json"
    root = Path(__file__).resolve().parents[2]
    vertical_ok, vertical_detail = _run(
        [
            sys.executable,
            str(root / "scripts/benchmarks/okx_full_vertical_slice.py"),
            "--model", args.model,
            "--max-addresses", "1",
            "--max-pages", "1",
            "--max-cases", "6",
            "--concurrency", "3",
            "--max-attempts", "2",
            "--strict",
            "--output", str(vertical_path),
        ]
    )
    chaos_ok, chaos_detail = _run(
        [
            sys.executable,
            str(root / "scripts/benchmarks/projector_failover_chaos_live.py"),
            "--dsn", args.dsn,
            "--bolt-uri", args.bolt_uri,
            "--graph-password", args.graph_password,
            "--etcd", args.etcd,
            "--events", str(args.events),
            "--lease-ttl", "3",
            "--output", str(chaos_path),
        ]
    )
    vertical = {}
    if vertical_path.exists():
        with open(vertical_path, "r", encoding="utf-8") as f:
            vertical = json.load(f)
    chaos = {}
    if chaos_path.exists():
        with open(chaos_path, "r", encoding="utf-8") as f:
            chaos = json.load(f)
    telemetry = {
        "prometheus": _healthy(args.prometheus + "/-/ready"),
        "tempo": _healthy(args.tempo + "/ready"),
        "grafana": _healthy(args.grafana + "/api/health"),
    }
    gates = evaluate(vertical, chaos, telemetry)
    report = {
        "schema_version": "seocho.okx-release-gate.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "model": args.model,
        "gates": gates,
        "passed": vertical_ok and chaos_ok and all(gates.values()),
        "telemetry": telemetry,
        "artifacts": {"vertical": vertical_path.name, "chaos": chaos_path.name},
        "diagnostics": {
            "vertical": "" if vertical_ok else vertical_detail,
            "chaos": "" if chaos_ok else chaos_detail,
        },
    }
    report_path = args.output_dir / "release-verdict.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
