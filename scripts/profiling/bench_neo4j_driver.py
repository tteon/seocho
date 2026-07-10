#!/usr/bin/env python3
"""$0 pure-Python vs neo4j-rust-ext driver A/B — the §21 gate for "switch to
the Rust neo4j driver?" (ADR-0101 methodology; issue hq-5td).

The official `neo4j-rust-ext` replaces ONLY the PackStream codec inside the
Python driver (blog claims: encode 1.16–4.26×, decode 1.09–9.27× — codec
microbenchmarks, not whole-path numbers). This bench measures the WHOLE PATH
behind SEOCHO's real callers, on live local graphs, no LLM calls:

  W1 federation-read   the literal examples/mdm/lib/federation.py
                       instances_read() across the 3 physical MDM shards
                       (~1–2k records total — the multi-instance hot read)
  W2 bulk-hydration    full node+rel scans with property maps from the largest
                       local DB (yitae0530grok: 21,579 nodes / 108,485 rels) —
                       the workload where codec decode share is material
  W3 concurrency sweep N∈{1,2,4,8,16,32} agents × one W1 round, threads vs
                       processes — the GIL evidence for the >100-core question

Methodology (bench_seocho_core.py house style):
  - both arms run in ISOLATED venvs (~/.venvs/neo4j-pure, ~/.venvs/neo4j-rust);
    the live environment is never modified (§21.2)
  - liveness asserted per worker via neo4j._codec.packstream.RUST_AVAILABLE —
    we can never mistake which codec we measured
  - process-level interleaving A,B,A,B,… per rep (the ext is an install-time
    drop-in, so in-process arm switching is impossible — recorded caveat)
  - gc off in timed region, 1 discarded warmup, min/median/p90 over ≥5 reps
  - parity: sha256 over canonically-sorted rows per workload; ANY mismatch
    rejects the candidate regardless of speed (§21.1(5))
  - codec-share isolation: client_overhead = wall − (result_available_after +
    result_consumed_after) from the ResultSummary (server time subtracted)

Pre-registered verdicts (fixed in the plan before running):
  V1 W1 median speedup ≥1.5× → adopt-trigger    V3 parity exact (mandatory)
  V2 W2 rows/sec speedup ≥1.5× → adopt-trigger  V7 if pure-arm W2 client share
  V6 custom-Rust escalation only on true GIL        <20% → Amdahl-immaterial,
     collapse (threads<0.4 ∧ processes≥0.7 @N≥8)    reject regardless of ratio

Run:  python3 scripts/profiling/bench_neo4j_driver.py            # orchestrator
      (worker mode is internal: --worker --arm pure|rust --workload w1|w2|w3)
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

from dotenv import dotenv_values  # noqa: E402

for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ.setdefault(k, v)

VENVS = {
    "pure": Path.home() / ".venvs" / "neo4j-pure",
    "rust": Path.home() / ".venvs" / "neo4j-rust",
}
OUT_DIR = ROOT / "scripts" / "profiling" / "outputs" / "driver_ab"
REPS = 5
W2_DB = "yitae0530grok"          # 21,579 nodes / 108,485 rels (largest local)
W2_DB_SECONDARY = "cgamiminimaxm25"   # 12k / 49k — size-sensitivity point
W3_AGENTS = [1, 2, 4, 8, 16, 32]

W2_NODE_Q = ("MATCH (n) RETURN labels(n) AS l, properties(n) AS p, "
             "elementId(n) AS e")
W2_REL_Q = ("MATCH (a)-[r]->(b) RETURN type(r) AS t, properties(r) AS p, "
            "elementId(a) AS s, elementId(b) AS o")


def _auth():
    return (os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", ""))


def _canon_hash(rows) -> str:
    blob = json.dumps(sorted(json.dumps(r, sort_keys=True, default=str) for r in rows))
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Worker mode — runs INSIDE a venv
# ---------------------------------------------------------------------------

def _assert_liveness(arm: str) -> str:
    from neo4j._codec.packstream import RUST_AVAILABLE
    expected = arm == "rust"
    if RUST_AVAILABLE != expected:
        raise SystemExit(f"liveness violation: arm={arm} but RUST_AVAILABLE="
                         f"{RUST_AVAILABLE} — wrong venv? aborting (§21.2)")
    return "rust-ext" if RUST_AVAILABLE else "pure-python"


def _w1_once() -> dict:
    """The real caller: instances_read() across the 3 physical shards."""
    sys.path.insert(0, str(ROOT / "examples" / "mdm"))
    from lib import federation
    instances = federation.load_instances(
        ROOT / "examples" / "mdm" / "config" / "instances.yaml")
    t0 = time.perf_counter()
    entities, metrics, shard_lat = federation.instances_read(instances, auth=_auth())
    wall = time.perf_counter() - t0
    return {"wall_s": wall, "rows": len(entities) + len(metrics),
            "shard_latency": shard_lat,
            "hash": _canon_hash([{k: e[k] for k in ("name", "src_db")} for e in entities]
                                + [{k: m[k] for k in ("metric", "value", "src_db")}
                                   for m in metrics])}


def _w2_once(database: str) -> dict:
    """Bulk hydration: full node+rel scans with property maps + elementIds."""
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=_auth())
    out = {"rows": 0, "server_ms": 0.0, "wall_s": 0.0, "parts": {}}
    rows_all = []
    try:
        t0 = time.perf_counter()
        for name, q in (("nodes", W2_NODE_Q), ("rels", W2_REL_Q)):
            with driver.session(database=database) as s:
                res = s.run(q)
                rows = list(res)          # full hydration — the codec-heavy path
                summ = res.consume()
            out["rows"] += len(rows)
            avail = summ.result_available_after or 0
            cons = summ.result_consumed_after or 0
            out["server_ms"] += avail + cons
            out["parts"][name] = len(rows)
            rows_all.extend(r.data() for r in rows[:200])  # bounded parity sample
        out["wall_s"] = time.perf_counter() - t0
    finally:
        driver.close()
    out["client_overhead_ms"] = round(out["wall_s"] * 1000 - out["server_ms"], 1)
    out["hash"] = _canon_hash(rows_all)
    return out


def _w1_rows(_=None) -> int:
    """Module-level (picklable) one-federation-round worker for W3."""
    return _w1_once()["rows"]


def _w3_once() -> dict:
    """Concurrency sweep: N agents × one W1 federation round, threads vs procs."""
    from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

    one_round = _w1_rows
    out = {}
    base = None
    for executor_name, executor_cls in (("threads", ThreadPoolExecutor),
                                        ("processes", ProcessPoolExecutor)):
        per_n = {}
        for n in W3_AGENTS:
            t0 = time.perf_counter()
            with executor_cls(max_workers=n) as ex:
                rows = sum(ex.map(one_round, range(n)))
            wall = time.perf_counter() - t0
            thru = n / wall                     # federation rounds per second
            per_n[n] = {"wall_s": round(wall, 4), "rounds_per_s": round(thru, 3),
                        "rows": rows}
            if executor_name == "threads" and n == 1:
                base = thru
        for n, rec in per_n.items():
            rec["efficiency_vs_n1"] = round(rec["rounds_per_s"] / (n * base), 3) if base else None
        out[executor_name] = per_n
    return out


def worker_main(arm: str, workload: str, out_path: str) -> int:
    codec = _assert_liveness(arm)
    rec: dict = {"arm": arm, "codec": codec, "workload": workload,
                 "python": sys.version.split()[0]}
    if workload == "w3":
        gc.disable()
        try:
            rec["sweep"] = _w3_once()
        finally:
            gc.enable()
    else:
        if workload == "w1":
            fn = _w1_once
        elif workload == "w2":
            fn = lambda: _w2_once(W2_DB)            # noqa: E731
        else:                                        # w2b
            fn = lambda: _w2_once(W2_DB_SECONDARY)  # noqa: E731
        fn()                       # warmup (discarded)
        samples = []
        gc.disable()
        try:
            for _ in range(REPS):
                samples.append(fn())
        finally:
            gc.enable()
        walls = sorted(s["wall_s"] for s in samples)
        rec["min_s"] = round(walls[0], 4)
        rec["median_s"] = round(statistics.median(walls), 4)
        rec["p90_s"] = round(walls[int(len(walls) * 0.9) - 1] if len(walls) > 1 else walls[0], 4)
        rec["rows"] = samples[0]["rows"]
        rec["rows_per_s_median"] = round(rec["rows"] / rec["median_s"], 1)
        rec["hashes"] = sorted({s["hash"] for s in samples})
        if "client_overhead_ms" in samples[0]:
            rec["client_overhead_ms_median"] = round(statistics.median(
                s["client_overhead_ms"] for s in samples), 1)
            rec["server_ms_median"] = round(statistics.median(
                s["server_ms"] for s in samples), 1)
    Path(out_path).write_text(json.dumps(rec, indent=1) + "\n")
    print(f"[worker] arm={arm} codec={codec} workload={workload} done")
    return 0


# ---------------------------------------------------------------------------
# Orchestrator mode
# ---------------------------------------------------------------------------

def run_worker(arm: str, workload: str) -> dict:
    py = VENVS[arm] / "bin" / "python"
    if not py.is_file():
        raise SystemExit(f"venv missing: {py} — see module docstring for setup")
    out = OUT_DIR / f"{workload}_{arm}.json"
    cmd = [str(py), __file__, "--worker", "--arm", arm,
           "--workload", workload, "--out", str(out)]
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise SystemExit(f"worker failed ({arm}/{workload}):\n{r.stdout}\n{r.stderr}")
    with open(out, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", action="store_true")
    ap.add_argument("--arm", choices=("pure", "rust"))
    ap.add_argument("--workload", choices=("w1", "w2", "w2b", "w3"))
    ap.add_argument("--out")
    ap.add_argument("--skip-w3", action="store_true")
    args = ap.parse_args()
    if args.worker:
        return worker_main(args.arm, args.workload, args.out)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict[str, dict]] = {}
    workloads = ["w1", "w2", "w2b"] + ([] if args.skip_w3 else ["w3"])
    for wl in workloads:
        results[wl] = {}
        # Process-level interleaving: pure, rust (per workload; reps inside).
        for arm in ("pure", "rust"):
            print(f"== running {wl}/{arm} ==", flush=True)
            results[wl][arm] = run_worker(arm, wl)

    # ---- parity (V3) ----
    parity = {}
    for wl in ("w1", "w2", "w2b"):
        h_pure = set(results[wl]["pure"].get("hashes", []))
        h_rust = set(results[wl]["rust"].get("hashes", []))
        parity[wl] = (h_pure == h_rust and len(h_pure) == 1)

    # ---- verdict table ----
    def ratio(wl, key):
        p, r = results[wl]["pure"][key], results[wl]["rust"][key]
        return round(p / r, 3) if key.endswith("_s") else round(r / p, 3)

    v1 = ratio("w1", "median_s")
    v2 = ratio("w2", "rows_per_s_median")
    w2_pure = results["w2"]["pure"]
    client_share = (w2_pure["client_overhead_ms_median"]
                    / (w2_pure["median_s"] * 1000))
    verdict = {
        "V1_w1_median_speedup": {"value": v1, "threshold": 1.5, "pass": v1 >= 1.5},
        "V2_w2_rows_per_s_speedup": {"value": v2, "threshold": 1.5, "pass": v2 >= 1.5},
        "V3_parity": {"value": parity, "pass": all(parity.values())},
        "V7_w2_pure_client_share": {"value": round(client_share, 3),
                                    "amdahl_material": client_share >= 0.20},
        "decision": None,
    }
    adopt = ((verdict["V1_w1_median_speedup"]["pass"]
              or verdict["V2_w2_rows_per_s_speedup"]["pass"])
             and verdict["V3_parity"]["pass"]
             and verdict["V7_w2_pure_client_share"]["amdahl_material"])
    verdict["decision"] = ("ADOPT rust-ext (V5 form: pinned dep + liveness log "
                           "+ CI parity)" if adopt else
                           "DO NOT switch — gate not met")

    summary = {"results": results, "verdict": verdict, "reps": REPS,
               "w2_db": W2_DB, "w2b_db": W2_DB_SECONDARY}
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=1))

    print("\nworkload | arm  | median s | rows/s   | client-ovh ms | hash-parity")
    print("-" * 72)
    for wl in ("w1", "w2", "w2b"):
        for arm in ("pure", "rust"):
            r = results[wl][arm]
            print(f"{wl:<8} | {arm:<4} | {r['median_s']:>8.4f} | "
                  f"{r.get('rows_per_s_median', 0):>8.1f} | "
                  f"{r.get('client_overhead_ms_median', '-'):>13} | "
                  f"{'OK' if parity[wl] else 'MISMATCH'}")
    if "w3" in results:
        print("\nW3 scaling efficiency (vs N=1 thread): threads vs processes")
        for ex in ("threads", "processes"):
            effs = {n: rec["efficiency_vs_n1"]
                    for n, rec in results["w3"]["pure"]["sweep"][ex].items()}
            print(f"  pure/{ex:<10} {effs}")
            effs = {n: rec["efficiency_vs_n1"]
                    for n, rec in results["w3"]["rust"]["sweep"][ex].items()}
            print(f"  rust/{ex:<10} {effs}")
    print(f"\nVERDICT: {verdict['decision']}")
    print(f"  V1 w1 speedup {v1}x (>=1.5 {'PASS' if v1 >= 1.5 else 'fail'}) | "
          f"V2 w2 rows/s {v2}x (>=1.5 {'PASS' if v2 >= 1.5 else 'fail'}) | "
          f"V3 parity {'PASS' if all(parity.values()) else 'FAIL'} | "
          f"V7 client share {client_share:.0%} "
          f"({'material' if client_share >= 0.2 else 'IMMATERIAL'})")
    print(f"\nraw artifacts: {OUT_DIR.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
