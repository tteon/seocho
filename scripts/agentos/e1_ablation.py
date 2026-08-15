"""E1: does the execution layer earn its keep under concurrency?

The execution pillar's (b)-claim, as an ablation: the same workload, the
same graphs, two arms —

  governed    AgentOS.execute_query — tenancy pinned, admission capped,
              fail-closed reads; over-capacity calls become STRUCTURED
              rejections the caller sees.
  ungoverned  the raw store path with no gate: every session hits the
              database at once, and there is nothing to say no.

What the numbers should show if the layer matters: the governed arm holds
database-side concurrency at the cap and keeps the latency tail bounded at
the cost of explicit rejections; the ungoverned arm lets N-way contention
through and pays for it in the tail. If the tails match, the layer is
overhead — report that too.

Two query weights, because a gate only binds when work is heavy enough to
queue: ``light`` (single-hop fan-in) and ``heavy`` (two-hop expansion with
an aggregate, expensive at SF10).

Usage:
  python scripts/agentos/e1_ablation.py --password ... \
      --databases finbenchl10 --sessions 16 --out outputs/agentos/e1.json
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

QUERIES = {
    "light": (
        "MATCH (a:Account)-[t:TRANSFER]->(b:Account) "
        "WHERE a._workspace_id = $workspace_id "
        "RETURN a.acct_no AS src, b.acct_no AS dst, t.amount AS amount "
        "ORDER BY t.amount DESC LIMIT $limit"
    ),
    "heavy": (
        "MATCH (a:Account)-[t1:TRANSFER]->(b:Account)-[t2:TRANSFER]->(c:Account) "
        "WHERE a._workspace_id = $workspace_id "
        "RETURN a.acct_no AS origin, count(DISTINCT c) AS reach, "
        "sum(t2.amount) AS moved "
        "ORDER BY moved DESC LIMIT $limit"
    ),
}


class CountingStore:
    def __init__(self, inner) -> None:
        self._inner = inner
        self.inflight = 0
        self.max_seen = 0
        self._lock = threading.Lock()

    def query(self, *args, **kwargs):
        with self._lock:
            self.inflight += 1
            self.max_seen = max(self.max_seen, self.inflight)
        try:
            return self._inner.query(*args, **kwargs)
        finally:
            with self._lock:
                self.inflight -= 1


def _percentile(ordered, q: float):
    if not ordered:
        return None
    return round(ordered[min(int(len(ordered) * q), len(ordered) - 1)], 1)


def run_arm(*, arm: str, ontology, store, database: str, sessions: int,
            calls_per_session: int, max_inflight: int, weight: str,
            admission_wait_s: float) -> dict:
    from seocho.operating_layer import SeochoOS

    counting = CountingStore(store)
    os_layer = None
    if arm == "governed":
        os_layer = SeochoOS(ontology=ontology, graph_store=counting,
                           database=database, workspace_id="default",
                           max_inflight=max_inflight,
                           admission_wait_s=admission_wait_s, row_cap=50)

    latencies: list[float] = []
    outcomes: list[str] = []
    lock = threading.Lock()
    cypher = QUERIES[weight]

    def worker(index: int) -> None:
        session = os_layer.session(f"e1-{index}") if os_layer else None
        for _ in range(calls_per_session):
            started = time.perf_counter()
            try:
                if os_layer is not None:
                    payload = os_layer.execute_query(
                        session, cypher, json.dumps({"limit": 20}))
                    outcome = ("served" if "row_count" in payload
                               else json.loads(payload).get("error", "error"))
                else:
                    counting.query(cypher,
                                   params={"workspace_id": "default", "limit": 20},
                                   database=database,
                                   enforce_workspace_filter=True)
                    outcome = "served"
            except Exception as exc:
                outcome = type(exc).__name__
            elapsed = (time.perf_counter() - started) * 1000
            with lock:
                outcomes.append(outcome)
                if outcome == "served":
                    latencies.append(elapsed)

    threads = [threading.Thread(target=worker, args=(i,))
               for i in range(sessions)]
    started_wall = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall_s = time.perf_counter() - started_wall

    ordered = sorted(latencies)
    served = outcomes.count("served")
    rejected = outcomes.count("QueryAdmissionRejected")
    return {
        "arm": arm, "weight": weight, "database": database,
        "sessions": sessions, "max_inflight": max_inflight if os_layer else None,
        "served": served, "rejected_structured": rejected,
        "other_errors": len(outcomes) - served - rejected,
        "p50_ms": _percentile(ordered, 0.50),
        "p95_ms": _percentile(ordered, 0.95),
        "p99_ms": _percentile(ordered, 0.99),
        "store_max_concurrency": counting.max_seen,
        "wall_s": round(wall_s, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default="bolt://localhost:17687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", required=True)
    parser.add_argument("--databases", nargs="+", default=["finbenchl1", "finbenchl10"])
    parser.add_argument("--sessions", nargs="+", type=int, default=[4, 16])
    parser.add_argument("--calls-per-session", type=int, default=6)
    parser.add_argument("--max-inflight", type=int, default=4)
    parser.add_argument("--admission-wait-s", type=float, default=10.0)
    parser.add_argument("--weights", nargs="+", default=["light", "heavy"])
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
    for database in args.databases:
        for weight in args.weights:
            for sessions in args.sessions:
                for arm in ("ungoverned", "governed"):
                    cell = run_arm(arm=arm, ontology=ontology, store=store,
                                   database=database, sessions=sessions,
                                   calls_per_session=args.calls_per_session,
                                   max_inflight=args.max_inflight,
                                   weight=weight,
                                   admission_wait_s=args.admission_wait_s)
                    report.append(cell)
                    print(json.dumps(cell), flush=True)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
