"""S1: does the priority reserve prevent starvation under a normal-class flood?

The scheduling pillar's smallest honest experiment. A crowd of normal
sessions hammers the layer continuously; a few high-priority sessions
arrive periodically, as an interactive user would. Two arms, one knob:

  reserved=0   plain bounded admission — high waits in the same crowd
  reserved=K   PriorityAdmission holds K permits no normal call may occupy

Reported per class and arm: served/timeouts, wait-inclusive p50/p95, and
the Jain fairness index over per-session served counts within the normal
class (the reserve must protect the high class *without* skewing normals
against each other).

Usage:
  python scripts/agentos/s1_fairness.py --password ... --database finbenchl10
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

QUERY = (
    "MATCH (a:Account)-[t:TRANSFER]->(b:Account) "
    "WHERE a._workspace_id = $workspace_id "
    "RETURN a.acct_no AS src, sum(t.amount) AS total "
    "ORDER BY total DESC LIMIT $limit"
)


def _pct(ordered, q):
    if not ordered:
        return None
    return round(ordered[min(int(len(ordered) * q), len(ordered) - 1)], 1)


def run_arm(*, ontology, store, database: str, reserved: int,
            normals: int, highs: int, duration_s: float,
            max_inflight: int) -> dict:
    from seocho.operating_layer import SeochoOS

    os_layer = SeochoOS(ontology=ontology, graph_store=store,
                       database=database, workspace_id="default",
                       max_inflight=max_inflight, reserved_for_high=reserved,
                       admission_wait_s=1.0, row_cap=20)
    stop = threading.Event()
    records: list[tuple[str, str, str, float]] = []   # (class, session, outcome, ms)
    lock = threading.Lock()

    def call(session, klass: str) -> None:
        started = time.perf_counter()
        try:
            payload = os_layer.execute_query(session, QUERY,
                                             json.dumps({"limit": 20}))
            outcome = "served" if "row_count" in payload else "error"
        except Exception as exc:
            outcome = type(exc).__name__
        elapsed = (time.perf_counter() - started) * 1000
        with lock:
            records.append((klass, session.session_id, outcome, elapsed))

    def normal_worker(i: int) -> None:
        session = os_layer.session(f"normal-{i}", priority="normal")
        while not stop.is_set():
            call(session, "normal")

    def high_worker(i: int) -> None:
        session = os_layer.session(f"high-{i}", priority="high")
        while not stop.is_set():
            call(session, "high")
            time.sleep(0.15)          # interactive cadence, not a flood

    threads = ([threading.Thread(target=normal_worker, args=(i,))
                for i in range(normals)]
               + [threading.Thread(target=high_worker, args=(i,))
                  for i in range(highs)])
    for thread in threads:
        thread.start()
    time.sleep(duration_s)
    stop.set()
    for thread in threads:
        thread.join(timeout=15)

    out = {"reserved_for_high": reserved, "max_inflight": max_inflight,
           "normals": normals, "highs": highs, "duration_s": duration_s}
    for klass in ("high", "normal"):
        rows = [r for r in records if r[0] == klass]
        served = [r for r in rows if r[2] == "served"]
        lat = sorted(r[3] for r in served)
        out[klass] = {
            "calls": len(rows), "served": len(served),
            "timeouts": sum(1 for r in rows if r[2] == "QueryAdmissionRejected"),
            "p50_ms": _pct(lat, 0.50), "p95_ms": _pct(lat, 0.95),
        }
    # Jain index over per-session served counts, normal class
    counts = {}
    for klass, sid, outcome, _ in records:
        if klass == "normal" and outcome == "served":
            counts[sid] = counts.get(sid, 0) + 1
    values = list(counts.values())
    if values:
        out["normal_jain_index"] = round(
            (sum(values) ** 2) / (len(values) * sum(v * v for v in values)), 4)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default="bolt://localhost:17687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", required=True)
    parser.add_argument("--database", default="finbenchl10")
    parser.add_argument("--normals", type=int, default=12)
    parser.add_argument("--highs", type=int, default=2)
    parser.add_argument("--duration-s", type=float, default=20.0)
    parser.add_argument("--max-inflight", type=int, default=4)
    parser.add_argument("--reserved", nargs="+", type=int, default=[0, 2])
    parser.add_argument("--ontology",
                        default=str(REPO / "examples/finbench/finbench.ontology.yaml"))
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    import yaml

    from seocho.ontology import Ontology
    from seocho.store.graph import Neo4jGraphStore

    ontology = Ontology.from_dict(yaml.safe_load(Path(args.ontology).read_text()))
    store = Neo4jGraphStore(uri=args.uri, user=args.user, password=args.password)

    report = []
    for reserved in args.reserved:
        cell = run_arm(ontology=ontology, store=store, database=args.database,
                       reserved=reserved, normals=args.normals,
                       highs=args.highs, duration_s=args.duration_s,
                       max_inflight=args.max_inflight)
        report.append(cell)
        print(json.dumps(cell), flush=True)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
