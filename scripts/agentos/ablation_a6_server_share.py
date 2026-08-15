"""Ablation A6 — I/O plane: server_share, the bolt-rs go/no-go gate.

Ablation row A6 of the OS study (wiki/os-ablation-study-design.md, seocho-xju)
and the ADR-0163 gate for further data-plane Rust. execute_query splits into a
control plane (Python governance: cypher hash, lane classify, scope enforcement,
admission acquire/release, EWMA observe, JSON serialize) and a data plane (the
Bolt round-trip + PackStream decode). We measure

    server_share = t_data / (t_data + t_control)

on the live DozerDB. Decision rule: if the data plane dominates, a native Rust
driver (neo4j-bolt-rs) pays; if control-plane Python or LLM wait dominates, it
does not. NOTE: SEOCHO already adopted the Rust PackStream *codec*
(neo4j-rust-ext, ADR-0111) — this measures how much data-plane time remains and
whether a full native driver is worth chasing beyond that.

Usage:
  python scripts/agentos/ablation_a6_server_share.py --container graphrag-neo4j \
      --database finbenchl10 --out outputs/agentos/ablation_a6_server_share.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from seocho.store.graph import (enforce_read_workspace_scope,  # noqa: E402
                                packstream_codec)

# Query classes: a light point-ish read and a heavier traversal returning many
# rows (real decode cost). Both reference $workspace_id so the ON governance path
# (enforce_read_workspace_scope + binding verification) accepts them.
_QUERIES = {
    "light": "MATCH (n:Account) WHERE n._workspace_id = $workspace_id "
             "RETURN n LIMIT 25",
    "heavy": "MATCH (a:Account)-[t:TRANSFER]->(b:Account) "
             "WHERE a._workspace_id = $workspace_id "
             "RETURN a.id AS s, b.id AS d, t.amount AS amt LIMIT 2000",
}


def auth_of(container: str):
    out = subprocess.check_output(
        ["docker", "inspect", container, "--format",
         "{{range .Config.Env}}{{println .}}{{end}}"]).decode()
    for line in out.splitlines():
        if line.startswith("NEO4J_AUTH="):
            u, p = line[len("NEO4J_AUTH="):].split("/", 1)
            return u, p
    raise SystemExit(f"no NEO4J_AUTH on {container}")


def _control_plane_ns(cypher: str, rows: List[Dict[str, Any]]) -> int:
    """Time the real governance steps execute_query runs around the query."""
    from seocho.operating_layer import LaneScheduler

    sched = LaneScheduler(max_inflight=8)
    params = {"workspace_id": "acme", "ws": "acme"}
    t0 = time.perf_counter_ns()
    key = hashlib.blake2b(" ".join(cypher.split()).encode(),
                          digest_size=8).hexdigest()
    lane = sched.classify(key)
    try:
        enforce_read_workspace_scope(cypher)
    except Exception:
        pass
    sched.acquire(lane=lane, priority="normal")
    sched.release(lane=lane, priority="normal")
    sched.observe(key, 1.0, lane=lane)
    _ = json.dumps({"rows": rows[:50], "row_count": min(len(rows), 50),
                    "truncated": len(rows) > 50}, default=str)
    _ = params
    return time.perf_counter_ns() - t0


def measure(container, uri, database, *, warmup=5, iters=40) -> Dict[str, Any]:
    from neo4j import GraphDatabase

    u, p = auth_of(container)
    drv = GraphDatabase.driver(uri, auth=(u, p))
    out = {"database": database, "codec": packstream_codec(), "classes": {}}
    try:
        # Auto-detect the workspace value present in this database.
        with drv.session(database=database, default_access_mode="READ") as s:
            ws = s.run("MATCH (n:Account) WHERE n._workspace_id IS NOT NULL "
                       "RETURN n._workspace_id AS w LIMIT 1").single()
        workspace = ws["w"] if ws else "default"
        out["workspace_id"] = workspace
        for name, cypher in _QUERIES.items():
            params = {"workspace_id": workspace}
            # warmup
            with drv.session(database=database, default_access_mode="READ") as s:
                for _ in range(warmup):
                    _ = [r.data() for r in s.run(cypher, parameters=params)]
            # data plane: Bolt round-trip + decode
            data_ns: List[int] = []
            rows: List[Dict[str, Any]] = []
            with drv.session(database=database, default_access_mode="READ") as s:
                for _ in range(iters):
                    t0 = time.perf_counter_ns()
                    rows = [r.data() for r in s.run(cypher, parameters=params)]
                    data_ns.append(time.perf_counter_ns() - t0)
            # control plane: the governance wrapper, over a representative result
            ctrl_ns = [_control_plane_ns(cypher, rows) for _ in range(iters)]

            data_ns.sort()
            ctrl_ns.sort()
            d_med = data_ns[len(data_ns) // 2] / 1e6      # ms
            c_med = ctrl_ns[len(ctrl_ns) // 2] / 1e6
            out["classes"][name] = {
                "rows": len(rows),
                "data_ms_median": round(d_med, 4),
                "control_ms_median": round(c_med, 4),
                "server_share": round(d_med / (d_med + c_med), 4),
                "control_share": round(c_med / (d_med + c_med), 4),
            }
    finally:
        drv.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--container", default="graphrag-neo4j")
    ap.add_argument("--uri", default="bolt://localhost:7687")
    ap.add_argument("--database", default="finbenchl10")
    ap.add_argument("--iters", type=int, default=40)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rep = measure(args.container, args.uri, args.database, iters=args.iters)
    print(f"=== A6 server_share on {rep['database']} (codec: {rep['codec']}) ===")
    print(f"  {'class':6s} {'rows':>6s} {'data ms':>9s} {'control ms':>11s} "
          f"{'server_share':>13s}")
    for name, c in rep["classes"].items():
        print(f"  {name:6s} {c['rows']:>6d} {c['data_ms_median']:>9.3f} "
              f"{c['control_ms_median']:>11.4f} {c['server_share']:>12.1%}")
    shares = [c["server_share"] for c in rep["classes"].values()]
    ctrl_ms = max(c["control_ms_median"] for c in rep["classes"].values())
    if min(shares) > 0.9:
        verdict = (f"governance overhead is negligible (~{ctrl_ms:.3f}ms, "
                   f"{100 * (1 - min(shares)):.1f}% at most) → the OS control "
                   "plane is nearly free; the data plane dominates, but the "
                   "rust-ext codec (ADR-0111) already captured the realized "
                   "lever there — a further native bolt-rs driver targets an "
                   "already-accelerated plane and needs its own A/B before it "
                   "is a go")
    else:
        verdict = ("control-plane / fixed overhead is non-trivial → weigh "
                   "bolt-rs against it")
    rep["verdict"] = verdict
    print(f"\n  verdict: {verdict}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
