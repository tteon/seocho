"""Ablation L1 — integrated OS-vs-bare outcome vector, on live DozerDB.

Level-1 headline of the OS study (wiki/os-ablation-study-design.md, seocho-41a).
Level-2 measured each subsystem alone; this runs ONE mixed, concurrent,
two-tenant workload through a BARE path (raw graph tool, no governance) vs the
OS path (`SeochoOS.execute_query`: admission + tenancy pin + scope enforcement +
row-cap disclosure) and reports the composed outcome vector — the claim that the
guarantees hold together, not just in isolation.

The task-correctness axis (does an LLM agent still answer right?) needs an agent
+ judge and is the follow-up; this measures the governance axes that don't need a
model, on real data.

Usage:
  python scripts/agentos/ablation_l1_integrated.py --container graphrag-neo4j \
      --workers 12 --queries 8 --out outputs/agentos/ablation_l1_integrated.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

_LABEL = "_AblL1Node"
_TENANT = "acme"
_OTHER = "globex"
_PER_TENANT = 120
_CAP = 50

# Mixed workload: a scoped-but-over-cap read (disclosure axis) and an
# adversarial cross-tenant read (isolation axis), both nominally scoped to acme.
_SCOPED_OVER_CAP = (f"MATCH (n:{_LABEL}) WHERE n._workspace_id = $workspace_id "
                    "RETURN n LIMIT 200")
_ADVERSARIAL = (f"MATCH (n:{_LABEL}),(m:{_LABEL}) "
                "WHERE m._workspace_id = $workspace_id RETURN n LIMIT 200")


def auth_of(container: str):
    out = subprocess.check_output(
        ["docker", "inspect", container, "--format",
         "{{range .Config.Env}}{{println .}}{{end}}"]).decode()
    for line in out.splitlines():
        if line.startswith("NEO4J_AUTH="):
            u, p = line[len("NEO4J_AUTH="):].split("/", 1)
            return u, p
    raise SystemExit(f"no NEO4J_AUTH on {container}")


class _CountingStore:
    """Wrap the real Neo4jGraphStore to observe max concurrency at the store."""

    def __init__(self, inner):
        self.inner = inner
        self.inflight = 0
        self.max_seen = 0
        self._lock = threading.Lock()

    def query(self, cypher, **kw):
        with self._lock:
            self.inflight += 1
            self.max_seen = max(self.max_seen, self.inflight)
        try:
            return self.inner.query(cypher, **kw)
        finally:
            with self._lock:
                self.inflight -= 1


def _leaked(rows: List[Dict[str, Any]]) -> int:
    n = 0
    for r in rows:
        node = r.get("n", r)
        ws = node.get("_workspace_id") if isinstance(node, dict) else None
        if ws is not None and ws != _TENANT:
            n += 1
    return n


def run(container, uri, database, workers, queries) -> Dict[str, Any]:
    from neo4j import GraphDatabase

    from seocho.operating_layer import SeochoOS
    from seocho.ontology import NodeDef, Ontology, P
    from seocho.store.graph import Neo4jGraphStore

    u, p = auth_of(container)
    setup = GraphDatabase.driver(uri, auth=(u, p))
    with setup.session(database=database) as s:
        s.run(f"MATCH (n:{_LABEL}) DETACH DELETE n")
        for i in range(_PER_TENANT):
            s.run(f"CREATE (n:{_LABEL} {{id:$i, _workspace_id:$t}})",
                  i=f"{_TENANT}-{i}", t=_TENANT)
            s.run(f"CREATE (n:{_LABEL} {{id:$i, _workspace_id:$t}})",
                  i=f"{_OTHER}-{i}", t=_OTHER)

    onto = Ontology(name="l1", graph_model="lpg",
                    nodes={_LABEL: NodeDef(properties={"id": P(str)})},
                    relationships={})
    store = Neo4jGraphStore(uri, u, p)

    def workload():
        # each worker alternates the two query shapes
        for q in range(queries):
            yield (_ADVERSARIAL if q % 2 else _SCOPED_OVER_CAP)

    def bare_arm():
        cstore = _CountingStore(store)
        leaks = [0]
        disclosed = [0]
        overcap = [0]
        lat: List[float] = []
        llock = threading.Lock()

        def worker():
            for cy in workload():
                t0 = time.perf_counter()
                rows = cstore.query(cy, params={"workspace_id": _TENANT},
                                    database=database, enforce_workspace_filter=False)
                dt = (time.perf_counter() - t0) * 1000
                with llock:
                    lat.append(dt)
                    leaks[0] += _leaked(rows)
                    if len(rows) > _CAP:        # a bare tool returns them all,
                        overcap[0] += 1         # with no truncation signal →
                        # disclosed stays 0: silent.
        _run_workers(worker, workers)
        return _summ("bare", leaks[0], disclosed[0], overcap[0], lat,
                     cstore.max_seen, rejected=0)

    def os_arm():
        cstore = _CountingStore(store)
        os_layer = SeochoOS(ontology=onto, graph_store=cstore, database=database,
                            workspace_id=_TENANT, max_inflight=4, row_cap=_CAP,
                            admission_wait_s=2.0)
        leaks = [0]
        disclosed = [0]
        overcap = [0]
        rejected = [0]
        lat: List[float] = []
        llock = threading.Lock()

        def worker():
            session = os_layer.session()
            for cy in workload():
                t0 = time.perf_counter()
                payload = json.loads(os_layer.execute_query(session, cy))
                dt = (time.perf_counter() - t0) * 1000
                with llock:
                    lat.append(dt)
                    if "error" in payload:
                        if "AdmissionRejected" in payload.get("error", ""):
                            rejected[0] += 1
                        continue
                    leaks[0] += _leaked(payload.get("rows", []))
                    if payload.get("truncated"):
                        overcap[0] += 1
                        disclosed[0] += 1        # governed path signals it
        _run_workers(worker, workers)
        return _summ("os", leaks[0], disclosed[0], overcap[0], lat,
                     cstore.max_seen, rejected=rejected[0])

    try:
        bare = bare_arm()
        os_res = os_arm()
    finally:
        with setup.session(database=database) as s:
            s.run(f"MATCH (n:{_LABEL}) DETACH DELETE n")
        setup.close()
        store.close() if hasattr(store, "close") else None
    return {"workers": workers, "queries_each": queries, "row_cap": _CAP,
            "max_inflight_os": 4, "bare": bare, "os": os_res}


def _run_workers(fn, n):
    ts = [threading.Thread(target=fn) for _ in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=60)


def _pctl(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 2)


def _summ(name, leaks, disclosed, overcap, lat, max_conc, rejected):
    return {
        "arm": name,
        "cross_tenant_leaks": leaks,
        "over_cap_results": overcap,
        "truncation_disclosed": disclosed,
        "disclosure_rate": round(disclosed / overcap, 3) if overcap else None,
        "max_store_concurrency": max_conc,
        "admission_rejected": rejected,
        "p50_ms": _pctl(lat, 0.50), "p95_ms": _pctl(lat, 0.95),
        "p99_ms": _pctl(lat, 0.99), "served": len(lat),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--container", default="graphrag-neo4j")
    ap.add_argument("--uri", default="bolt://localhost:7687")
    ap.add_argument("--database", default="neo4j")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--queries", type=int, default=8)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rep = run(args.container, args.uri, args.database, args.workers, args.queries)
    print("=== L1 integrated: BARE vs OS on one mixed 2-tenant concurrent load ===")
    print(f"  {args.workers} workers x {args.queries} queries; cap={rep['row_cap']}; "
          f"OS max_inflight={rep['max_inflight_os']}\n")
    hdr = ("axis", "BARE", "OS")
    print(f"  {hdr[0]:26s} {hdr[1]:>12s} {hdr[2]:>12s}")
    b, o = rep["bare"], rep["os"]
    rows = [
        ("cross-tenant leaks", b["cross_tenant_leaks"], o["cross_tenant_leaks"]),
        ("truncation disclosure", b["disclosure_rate"], o["disclosure_rate"]),
        ("max store concurrency", b["max_store_concurrency"], o["max_store_concurrency"]),
        ("admission rejected", b["admission_rejected"], o["admission_rejected"]),
        ("p99 ms", b["p99_ms"], o["p99_ms"]),
    ]
    for label, bv, ov in rows:
        print(f"  {label:26s} {str(bv):>12s} {str(ov):>12s}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
