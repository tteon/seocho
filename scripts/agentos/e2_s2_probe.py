"""E2/S2: scheduler v2's claims, measured p99-first on live graphs.

E2 — with heavy traffic in the background, what happens to the LIGHT
class's p99? Arms differ only in lanes: single-lane v1 posture
(light_permits=0: everyone shares 4 permits FIFO) vs v2 lanes
(light: 2 / heavy: 2). EWMA is warmed with one observation per statement
first — the cold-start (unknown-means-heavy) is a stated policy, not what
this probe measures.

S2 — v1's static reserve taxed normal throughput even while the high
class was idle. Same mixed workload as S1 but with an explicit high-idle
phase; arms: static reserve (PriorityAdmission) vs work-conserving
reserve (LaneScheduler borrow). High starvation must stay zero while
normal throughput in the idle phase recovers.
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

LIGHT_Q = ("MATCH (a:Account)-[t:TRANSFER]->(b:Account) "
           "WHERE a._workspace_id = $workspace_id "
           "RETURN a.acct_no AS src, t.amount AS amount "
           "ORDER BY t.amount DESC LIMIT $limit")
HEAVY_Q = ("MATCH (a:Account)-[t1:TRANSFER]->(b:Account)-[t2:TRANSFER]->(c:Account) "
           "WHERE a._workspace_id = $workspace_id "
           "RETURN a.acct_no AS origin, count(DISTINCT c) AS reach, "
           "sum(t2.amount) AS moved ORDER BY moved DESC LIMIT $limit")


def _pct(ordered, q):
    if not ordered:
        return None
    return round(ordered[min(int(len(ordered) * q), len(ordered) - 1)], 1)


def _stats(rows):
    lat = sorted(r[2] for r in rows if r[1] == "served")
    return {"calls": len(rows), "served": len(lat),
            "rejected": sum(1 for r in rows if r[1] != "served"),
            "p50_ms": _pct(lat, 0.50), "p95_ms": _pct(lat, 0.95),
            "p99_ms": _pct(lat, 0.99)}


def build_layer(ontology, store, database, *, light_permits, reserved=0,
                static_reserve=False):
    from seocho.operating_layer import PriorityAdmission, SeochoOS

    layer = SeochoOS(ontology=ontology, graph_store=store, database=database,
                     workspace_id="default", max_inflight=4,
                     light_permits=light_permits, reserved_for_high=reserved,
                     admission_wait_s=2.0, row_cap=20)
    if static_reserve:
        # v1 posture for the S2 comparison: the non-borrowable reserve.
        gate = PriorityAdmission(max_inflight=4, reserved_for_high=reserved,
                                 wait_seconds=2.0)

        class _V1Adapter:
            max_inflight = 4

            def classify(self, key):
                return "heavy"

            def observe(self, key, ms, lane=None):
                pass

            def acquire(self, *, lane, priority, deadline_s=None):
                return gate.acquire(priority)

            def release(self, *, lane, priority):
                gate.release(priority)

        layer._admission = _V1Adapter()
    return layer


def warm(layer, session):
    layer.execute_query(session, LIGHT_Q, json.dumps({"limit": 20}))
    layer.execute_query(session, HEAVY_Q, json.dumps({"limit": 5}))


def run_e2(ontology, store, database, *, lanes: bool, duration_s: float) -> dict:
    layer = build_layer(ontology, store, database,
                        light_permits=2 if lanes else 0)
    warm(layer, layer.session("warm"))
    stop = threading.Event()
    records = {"light": [], "heavy": []}
    lock = threading.Lock()

    def worker(klass, query, limit):
        session = layer.session(f"{klass}-{threading.get_ident()}")
        while not stop.is_set():
            started = time.perf_counter()
            try:
                payload = layer.execute_query(session, query,
                                              json.dumps({"limit": limit}))
                outcome = "served" if "row_count" in payload else "error"
            except Exception as exc:
                outcome = type(exc).__name__
            with lock:
                records[klass].append(
                    (klass, outcome, (time.perf_counter() - started) * 1000))
            if outcome != "served":
                time.sleep(0.1)      # structured rejection => back off

    threads = ([threading.Thread(target=worker, args=("light", LIGHT_Q, 20))
                for _ in range(8)]
               + [threading.Thread(target=worker, args=("heavy", HEAVY_Q, 5))
                  for _ in range(4)])
    for thread in threads:
        thread.start()
    time.sleep(duration_s)
    stop.set()
    for thread in threads:
        thread.join(timeout=30)
    return {"arm": "lanes" if lanes else "single_lane_v1",
            "light": _stats(records["light"]), "heavy": _stats(records["heavy"])}


def run_s2(ontology, store, database, *, static: bool, phase_s: float) -> dict:
    layer = build_layer(ontology, store, database, light_permits=0,
                        reserved=2, static_reserve=static)
    if not static:
        warm(layer, layer.session("warm"))
    stop = threading.Event()
    high_on = threading.Event()
    high_on.set()
    records = []
    lock = threading.Lock()

    def normal_worker(i):
        session = layer.session(f"n{i}", priority="normal")
        while not stop.is_set():
            phase = "active" if high_on.is_set() else "idle"
            started = time.perf_counter()
            try:
                payload = layer.execute_query(session, LIGHT_Q,
                                              json.dumps({"limit": 20}))
                outcome = "served" if "row_count" in payload else "error"
            except Exception as exc:
                outcome = type(exc).__name__
            with lock:
                records.append(("normal", phase, outcome,
                                (time.perf_counter() - started) * 1000))
            if outcome != "served":
                time.sleep(0.1)      # structured rejection => back off

    def high_worker(i):
        session = layer.session(f"h{i}", priority="high")
        while not stop.is_set():
            if not high_on.is_set():
                time.sleep(0.05)
                continue
            started = time.perf_counter()
            try:
                payload = layer.execute_query(session, LIGHT_Q,
                                              json.dumps({"limit": 20}))
                outcome = "served" if "row_count" in payload else "error"
            except Exception as exc:
                outcome = type(exc).__name__
            with lock:
                records.append(("high", "active", outcome,
                                (time.perf_counter() - started) * 1000))
            time.sleep(0.15)

    threads = ([threading.Thread(target=normal_worker, args=(i,))
                for i in range(12)]
               + [threading.Thread(target=high_worker, args=(i,))
                  for i in range(2)])
    for thread in threads:
        thread.start()
    time.sleep(phase_s)          # phase 1: high active
    high_on.clear()
    time.sleep(phase_s)          # phase 2: high idle — conservation shows here
    stop.set()
    for thread in threads:
        thread.join(timeout=30)

    def klass_phase(klass, phase):
        rows = [(r[0], r[2], r[3]) for r in records
                if r[0] == klass and r[1] == phase]
        return _stats(rows)

    return {"arm": "static_reserve_v1" if static else "work_conserving_v2",
            "high_active": klass_phase("high", "active"),
            "normal_active": klass_phase("normal", "active"),
            "normal_idle_phase": klass_phase("normal", "idle")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default="bolt://localhost:17687")
    parser.add_argument("--password", required=True)
    parser.add_argument("--database", default="finbenchl10")
    parser.add_argument("--duration-s", type=float, default=15.0)
    parser.add_argument("--ontology",
                        default=str(REPO / "examples/finbench/finbench.ontology.yaml"))
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    import yaml

    from seocho.ontology import Ontology
    from seocho.store.graph import Neo4jGraphStore

    ontology = Ontology.from_dict(yaml.safe_load(Path(args.ontology).read_text()))
    store = Neo4jGraphStore(uri=args.uri, user="neo4j", password=args.password)

    report = {"e2": [], "s2": []}
    for lanes in (False, True):
        cell = run_e2(ontology, store, args.database, lanes=lanes,
                      duration_s=args.duration_s)
        report["e2"].append(cell)
        print(json.dumps(cell), flush=True)
    for static in (True, False):
        cell = run_s2(ontology, store, args.database, static=static,
                      phase_s=args.duration_s / 2)
        report["s2"].append(cell)
        print(json.dumps(cell), flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
