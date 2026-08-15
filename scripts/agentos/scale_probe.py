"""AgentOS scalability probe: N concurrent sessions, one layer, real graphs.

Scalability is this feature's acceptance test, so the probe drives the exact
governed path an SDK agent uses (``AgentOS.execute_query``) — pinning,
admission, fail-closed reads — with no model in the loop, across:

  - the SF axis: FinBench graphs (finbenchl1 / finbenchl10)
  - the concurrency axis: N sessions in {1, 4, 16}
  - the contention axis: max_inflight below N, so the shared gate binds

Reported per cell: served/rejected counts, p50/p95 wall latency of served
calls, and the store-side max concurrency actually observed (must never
exceed max_inflight — that single number is the execution pillar holding).

Usage:
  python scripts/agentos/scale_probe.py --password ... \
      --databases finbenchl1 finbenchl10 --sessions 1 4 16
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
    "RETURN a.acct_no AS src, b.acct_no AS dst, t.amount AS amount "
    "ORDER BY t.amount DESC LIMIT $limit"
)


class CountingStore:
    """Wrap the real store to observe true concurrency at the Bolt boundary."""

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


def run_cell(*, ontology, store, database: str, sessions: int,
             calls_per_session: int, max_inflight: int) -> dict:
    from seocho.operating_layer import SeochoOS

    counting = CountingStore(store)
    os_layer = SeochoOS(ontology=ontology, graph_store=counting,
                       database=database, workspace_id="default",
                       max_inflight=max_inflight, admission_wait_s=2.0,
                       row_cap=50)
    latencies: list[float] = []
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        session = os_layer.session(f"probe-{index}")
        for _ in range(calls_per_session):
            started = time.perf_counter()
            try:
                payload = os_layer.execute_query(
                    session, QUERY, json.dumps({"limit": 50}))
                outcome = "served" if "row_count" in payload else "error"
            except Exception as exc:
                outcome = type(exc).__name__
            elapsed = (time.perf_counter() - started) * 1000
            with lock:
                outcomes.append(outcome)
                if outcome == "served":
                    latencies.append(elapsed)

    threads = [threading.Thread(target=worker, args=(i,))
               for i in range(sessions)]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall_s = time.perf_counter() - started

    served = outcomes.count("served")
    ordered = sorted(latencies)
    return {
        "database": database, "sessions": sessions,
        "max_inflight": max_inflight,
        "served": served,
        "rejected": outcomes.count("QueryAdmissionRejected"),
        "errors": len(outcomes) - served - outcomes.count("QueryAdmissionRejected"),
        "p50_ms": round(ordered[len(ordered) // 2], 1) if ordered else None,
        "p95_ms": round(ordered[int(len(ordered) * 0.95) - 1], 1) if ordered else None,
        "store_max_concurrency": counting.max_seen,
        "bound_held": counting.max_seen <= max_inflight,
        "wall_s": round(wall_s, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default="bolt://localhost:17687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", required=True)
    parser.add_argument("--databases", nargs="+",
                        default=["finbenchl1", "finbenchl10"])
    parser.add_argument("--sessions", nargs="+", type=int, default=[1, 4, 16])
    parser.add_argument("--calls-per-session", type=int, default=8)
    parser.add_argument("--max-inflight", type=int, default=4)
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
        for sessions in args.sessions:
            cell = run_cell(ontology=ontology, store=store, database=database,
                            sessions=sessions,
                            calls_per_session=args.calls_per_session,
                            max_inflight=args.max_inflight)
            report.append(cell)
            print(json.dumps(cell), flush=True)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2))
    violations = [c for c in report if not c["bound_held"]]
    print(f"\ncells={len(report)} bound_violations={len(violations)}")
    if violations:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
