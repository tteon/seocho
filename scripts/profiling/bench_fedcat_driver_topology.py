#!/usr/bin/env python3
"""Driver/topology bottleneck breakdown for the hq-42k fedcat scenario.

Measures the same provider inventory read through two topology shapes:

* single_dbms: four provider databases on one DozerDB DBMS
* physical_instances: four provider DBMS endpoints

Each topology is run in isolated driver environments:

* pure: ~/.venvs/neo4j-pure
* rust: ~/.venvs/neo4j-rust

This keeps the "Python driver vs neo4j-rust-ext PackStream codec" comparison
honest: arm liveness is asserted via ``neo4j._codec.packstream.RUST_AVAILABLE``.
No LLM calls are made.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values  # noqa: E402

for key, value in dotenv_values(ROOT / ".env").items():
    if value is not None:
        os.environ.setdefault(key, value)

VENVS = {
    "pure": Path.home() / ".venvs" / "neo4j-pure",
    "rust": Path.home() / ".venvs" / "neo4j-rust",
}
OUT_DIR = ROOT / "scripts" / "profiling" / "outputs" / "fedcat_driver_topology"
REPS = 5

ENTITY_Q = """
MATCH (n)
WHERE n.name IS NOT NULL
  AND n.value IS NULL
  AND n._workspace_id STARTS WITH $workspace_prefix
RETURN labels(n) AS labels, properties(n) AS props, elementId(n) AS eid
"""

FACT_Q = """
MATCH (m)
WHERE m.name IS NOT NULL
  AND m.value IS NOT NULL
  AND m._workspace_id STARTS WITH $workspace_prefix
RETURN labels(m) AS labels, properties(m) AS props, elementId(m) AS eid
"""


def _auth() -> tuple[str, str]:
    return (
        os.environ.get("NEO4J_USER", "neo4j"),
        os.environ.get("NEO4J_PASSWORD", ""),
    )


def _load_instances(path: Path) -> list[dict]:
    import yaml

    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        {
            "provider_id": key,
            "uri": value["uri"],
            "database": value.get("database", "neo4j"),
            "model": value["model"],
        }
        for key, value in spec["instances"].items()
    ]


def _canon_hash(rows: list[dict]) -> str:
    blob = json.dumps(sorted(json.dumps(row, sort_keys=True, default=str) for row in rows))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def _assert_liveness(arm: str) -> str:
    from neo4j._codec.packstream import RUST_AVAILABLE

    expected = arm == "rust"
    if RUST_AVAILABLE != expected:
        raise SystemExit(
            f"liveness violation: arm={arm}, RUST_AVAILABLE={RUST_AVAILABLE}"
        )
    return "rust-ext" if RUST_AVAILABLE else "pure-python"


def _read_inventory(instances: list[dict]) -> dict:
    from neo4j import GraphDatabase

    rows_for_hash: list[dict] = []
    per_provider: dict[str, dict] = {}
    rows_total = 0
    server_ms = 0.0
    t0 = time.perf_counter()
    drivers: dict[str, object] = {}
    try:
        for inst in instances:
            driver = drivers.get(inst["uri"])
            if driver is None:
                driver = GraphDatabase.driver(inst["uri"], auth=_auth())
                drivers[inst["uri"]] = driver
            provider_rows = 0
            provider_server_ms = 0.0
            with driver.session(database=inst["database"]) as session:
                for kind, query in (("entity", ENTITY_Q), ("fact", FACT_Q)):
                    result = session.run(
                        query,
                        workspace_prefix=f"fedcat-{inst['provider_id']}-",
                    )
                    records = list(result)
                    summary = result.consume()
                    provider_rows += len(records)
                    provider_server_ms += (
                        (summary.result_available_after or 0)
                        + (summary.result_consumed_after or 0)
                    )
                    rows_for_hash.extend(
                        {
                            "provider_id": inst["provider_id"],
                            "database": inst["database"],
                            "kind": kind,
                            "eid": record["eid"],
                            "labels": record["labels"],
                            "props": record["props"],
                        }
                        for record in records
                    )
            rows_total += provider_rows
            server_ms += provider_server_ms
            per_provider[inst["provider_id"]] = {
                "rows": provider_rows,
                "server_ms": round(provider_server_ms, 1),
                "uri": inst["uri"],
                "database": inst["database"],
            }
    finally:
        for driver in drivers.values():
            driver.close()
    wall_s = time.perf_counter() - t0
    return {
        "wall_s": wall_s,
        "rows": rows_total,
        "server_ms": server_ms,
        "client_overhead_ms": round(wall_s * 1000 - server_ms, 1),
        "per_provider": per_provider,
        "hash": _canon_hash(rows_for_hash),
    }


def worker_main(arm: str, topology: str, out_path: str) -> int:
    codec = _assert_liveness(arm)
    if topology == "single_dbms":
        config = ROOT / "examples" / "mdm" / "config" / "provider_databases.yaml"
    elif topology == "physical_instances":
        config = ROOT / "examples" / "mdm" / "config" / "providers.yaml"
    else:
        raise SystemExit(f"unknown topology {topology!r}")
    instances = _load_instances(config)
    _read_inventory(instances)  # warmup
    samples = []
    gc.disable()
    try:
        for _ in range(REPS):
            samples.append(_read_inventory(instances))
    finally:
        gc.enable()
    walls = sorted(sample["wall_s"] for sample in samples)
    rec = {
        "arm": arm,
        "codec": codec,
        "topology": topology,
        "config": str(config.relative_to(ROOT)),
        "rows": samples[0]["rows"],
        "median_s": round(statistics.median(walls), 4),
        "min_s": round(walls[0], 4),
        "p90_s": round(walls[int(len(walls) * 0.9) - 1], 4),
        "rows_per_s_median": round(samples[0]["rows"] / statistics.median(walls), 1),
        "server_ms_median": round(statistics.median(sample["server_ms"] for sample in samples), 1),
        "client_overhead_ms_median": round(
            statistics.median(sample["client_overhead_ms"] for sample in samples), 1
        ),
        "hashes": sorted({sample["hash"] for sample in samples}),
        "per_provider": samples[0]["per_provider"],
    }
    Path(out_path).write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
    print(f"[worker] {topology}/{arm} codec={codec} done")
    return 0


def _run_worker(arm: str, topology: str) -> dict:
    py = VENVS[arm] / "bin" / "python"
    if not py.is_file():
        raise SystemExit(f"venv missing: {py}")
    out = OUT_DIR / f"{topology}_{arm}.json"
    cmd = [
        str(py),
        __file__,
        "--worker",
        "--arm",
        arm,
        "--topology",
        topology,
        "--out",
        str(out),
    ]
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    run = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=900)
    if run.returncode != 0:
        raise SystemExit(
            f"worker failed {topology}/{arm}\nSTDOUT:\n{run.stdout}\nSTDERR:\n{run.stderr}"
        )
    return json.loads(out.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--arm", choices=("pure", "rust"))
    parser.add_argument("--topology", choices=("single_dbms", "physical_instances"))
    parser.add_argument("--out")
    args = parser.parse_args()

    if args.worker:
        return worker_main(args.arm, args.topology, args.out)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict[str, dict]] = {}
    for topology in ("single_dbms", "physical_instances"):
        results[topology] = {}
        for arm in ("pure", "rust"):
            print(f"== running {topology}/{arm} ==", flush=True)
            results[topology][arm] = _run_worker(arm, topology)

    summary: dict[str, dict] = {}
    for topology, arms in results.items():
        pure, rust = arms["pure"], arms["rust"]
        parity = set(pure["hashes"]) == set(rust["hashes"]) and len(pure["hashes"]) == 1
        summary[topology] = {
            "rows": pure["rows"],
            "parity": parity,
            "pure_median_s": pure["median_s"],
            "rust_median_s": rust["median_s"],
            "wall_speedup_pure_over_rust": round(pure["median_s"] / rust["median_s"], 3),
            "pure_rows_per_s": pure["rows_per_s_median"],
            "rust_rows_per_s": rust["rows_per_s_median"],
            "rows_per_s_speedup": round(rust["rows_per_s_median"] / pure["rows_per_s_median"], 3),
            "pure_client_overhead_ms": pure["client_overhead_ms_median"],
            "rust_client_overhead_ms": rust["client_overhead_ms_median"],
            "pure_server_ms": pure["server_ms_median"],
            "rust_server_ms": rust["server_ms_median"],
        }

    single = summary["single_dbms"]
    physical = summary["physical_instances"]
    topology_compare = {
        "pure_single_vs_physical_wall_ratio": round(
            single["pure_median_s"] / physical["pure_median_s"], 3
        ),
        "rust_single_vs_physical_wall_ratio": round(
            single["rust_median_s"] / physical["rust_median_s"], 3
        ),
    }
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": results,
        "summary": summary,
        "topology_compare": topology_compare,
    }
    out_path = OUT_DIR / "fedcat_driver_topology_summary.json"
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    print("\ntopology           | rows | parity | pure ms | rust ms | speedup | pure client | rust client")
    print("-" * 92)
    for topology, row in summary.items():
        print(
            f"{topology:<18} | {row['rows']:>4} | {str(row['parity']):<6} | "
            f"{row['pure_median_s'] * 1000:>7.1f} | {row['rust_median_s'] * 1000:>7.1f} | "
            f"{row['wall_speedup_pure_over_rust']:>7.2f} | "
            f"{row['pure_client_overhead_ms']:>11.1f} | {row['rust_client_overhead_ms']:>11.1f}"
        )
    print(f"\n== wrote {out_path.relative_to(ROOT)} ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
