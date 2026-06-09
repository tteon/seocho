#!/usr/bin/env python3
"""Graph-RAG e2e stage profiler — latency / bandwidth / CPU-vs-I/O bound.

Decomposes the Graph-RAG path into its real stages, runs them e2e against a
LIVE DozerDB (arbiter ON), and profiles each stage to answer: where is the
latency, how much data moves, and is each stage CPU-bound or I/O/wait-bound?

Methodology (the load-bearing part): per stage we measure BOTH
  wall = time.perf_counter()   (elapsed real time)
  cpu  = time.process_time()   (in-process CPU time)
A stage with cpu/wall >= 0.6 is CPU-bound (compute dominates); <= 0.3 is
I/O/wait-bound (blocked on a DB round-trip or a network LLM call); in between
is mixed. This is the principled way to separate compute from wait without a
profiler attached — perf_counter advances while blocked on I/O, process_time
does not. Bandwidth is the data each stage moves (candidate surfaces scored,
rows + bytes fetched, context chars, answer tokens).

Stages:
  intent       parse query -> required categories (rule-based)        [CPU]
  arbiter      select_ontology over manifests (closed-vocab + ground) [CPU]
  retrieve     Cypher subgraph fetch from DozerDB                      [I/O]
  evidence     assemble evidence text from rows (in-process)          [CPU]
  answer       MARA LLM call                                          [I/O]

Run: PYTHONPATH=src:scripts/benchmarks python3 scripts/benchmarks/graphrag_stage_profiler.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List

from neo4j import GraphDatabase

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "src", _ROOT, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from finder_arms import ANSWER_SPEC, answer  # noqa: E402
from finder_backbone import build_backbone, select_xcat_cases  # noqa: E402  (load a real graph)
from finder_intent_router import route  # noqa: E402
from examples.finder.lib import llm_io  # noqa: E402
from seocho.query.arbiter import OntologyManifest, select_ontology  # noqa: E402
from seocho.semantic_layer.concepts import default_registry  # noqa: E402
from seocho.semantic_layer.identity import EntityResolver  # noqa: E402

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except Exception:
    pass

CONTAINER = "seocho-profiler-neo4j"
PASSWORD = "seocho-dev"
IMAGE = "graphstack/dozerdb:5.26.3.0"
BOLT = "bolt://localhost:7697"
DB = "finderbackbone"
ITERS = 5


@dataclass
class StageStat:
    name: str
    kind_hint: str
    wall_ms: List[float] = field(default_factory=list)
    cpu_ms: List[float] = field(default_factory=list)
    bandwidth: str = ""

    def add(self, wall: float, cpu: float) -> None:
        self.wall_ms.append(wall)
        self.cpu_ms.append(cpu)

    @property
    def mean_wall(self) -> float:
        return sum(self.wall_ms) / len(self.wall_ms)

    @property
    def mean_cpu(self) -> float:
        return sum(self.cpu_ms) / len(self.cpu_ms)

    @property
    def bound(self) -> str:
        r = self.mean_cpu / self.mean_wall if self.mean_wall else 0.0
        return "CPU" if r >= 0.6 else ("I/O/wait" if r <= 0.3 else "mixed")


def timed(stat: StageStat, fn: Callable):
    w0, c0 = time.perf_counter(), time.process_time()
    out = fn()
    stat.add((time.perf_counter() - w0) * 1000.0, (time.process_time() - c0) * 1000.0)
    return out


def boot():
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--rm", "--name", CONTAINER,
                    "-e", f"NEO4J_AUTH=neo4j/{PASSWORD}", "-p", "7484:7474", "-p", "7697:7687", IMAGE],
                   capture_output=True, text=True)
    for _ in range(60):
        try:
            drv = GraphDatabase.driver(BOLT, auth=("neo4j", PASSWORD))
            with drv.session(database="system") as s:
                s.run("SHOW DATABASES YIELD name RETURN count(name)").single()
                s.run(f"CREATE DATABASE `{DB}` IF NOT EXISTS").consume()
            time.sleep(3)
            return drv
        except Exception:
            time.sleep(2)
    return None


def main() -> int:
    resolver = EntityResolver.from_frozen()
    if resolver is None:
        print("FATAL: frozen CIK table not found", file=sys.stderr)
        return 1
    cases = select_xcat_cases(resolver)
    cik = cases[0].cik
    question = cases[0].query
    manifests = [OntologyManifest("finance", default_registry(), resolver)]

    drv = boot()
    if drv is None:
        print("FATAL: DozerDB not ready", file=sys.stderr)
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
        return 1
    aclient = llm_io.make_chat_client(llm_io.parse_llm_spec(ANSWER_SPEC))
    amodel = llm_io.parse_llm_spec(ANSWER_SPEC).model
    try:
        build_backbone(drv, cases)
        stats = {
            "intent": StageStat("intent", "CPU"),
            "arbiter": StageStat("arbiter", "CPU"),
            "retrieve": StageStat("retrieve", "I/O"),
            "evidence": StageStat("evidence", "CPU"),
            "answer": StageStat("answer", "I/O"),
        }
        # warm caches (model load, JIT, connection) so steady-state is measured
        route(question); select_ontology("revenue", manifests)
        with drv.session(database=DB) as s:
            s.run("MATCH (c:Company {cik:$c}) RETURN c.cik", c=cik).consume()

        print("=" * 86)
        print(f"Graph-RAG stage profiler — arbiter ON, live DozerDB; {ITERS} iters")
        print(f"question: {question[:70]}")
        print("=" * 86)
        for _ in range(ITERS):
            timed(stats["intent"], lambda: route(question))
            m = timed(stats["arbiter"], lambda: select_ontology("revenue growth", manifests))

            def _retrieve():
                with drv.session(database=DB) as s:
                    return s.run(
                        "MATCH (c:Company {cik:$c})-[:FOR_YEAR]->(:CompanyYear)"
                        "-[:HAS_SECTION]->(fs)-[:CONTAINS]->(e:Evidence) "
                        "RETURN fs.kind AS kind, e.text AS text", c=cik).data()
            rows = timed(stats["retrieve"], _retrieve)

            ctx = timed(stats["evidence"],
                        lambda: "\n\n".join(f"[{r['kind']}] {r['text']}" for r in rows)[:2200])
            ans = timed(stats["answer"], lambda: answer(aclient, amodel, question, ctx))

        # bandwidth signals (last iteration)
        stats["arbiter"].bandwidth = f"{len(list(manifests[0].registry.candidate_surfaces))} surfaces scored"
        stats["retrieve"].bandwidth = f"{len(rows)} rows, {sum(len(r['text']) for r in rows)} bytes"
        stats["evidence"].bandwidth = f"{len(ctx)} ctx chars"
        stats["answer"].bandwidth = f"~{len(ans)//4} out tokens, ~{len(ctx)//4} in tokens"

        print(f"\n  {'stage':<10}{'mean_wall_ms':>13}{'mean_cpu_ms':>12}{'cpu/wall':>9}"
              f"{'bound':>10}   bandwidth")
        print("  " + "-" * 84)
        total = 0.0
        for st in stats.values():
            r = st.mean_cpu / st.mean_wall if st.mean_wall else 0.0
            total += st.mean_wall
            print(f"  {st.name:<10}{st.mean_wall:>13.1f}{st.mean_cpu:>12.1f}{r:>9.2f}"
                  f"{st.bound:>10}   {st.bandwidth}")
        print("  " + "-" * 84)
        print(f"  {'TOTAL':<10}{total:>13.1f} ms/query")
        bottleneck = max(stats.values(), key=lambda s: s.mean_wall)
        print(f"\n  Bottleneck: '{bottleneck.name}' ({bottleneck.mean_wall:.0f}ms, "
              f"{bottleneck.mean_wall/total*100:.0f}% of wall, {bottleneck.bound}).")
        io = [s.name for s in stats.values() if s.bound == "I/O/wait"]
        cpu = [s.name for s in stats.values() if s.bound == "CPU"]
        print(f"  I/O/wait-bound: {io or '—'}  |  CPU-bound: {cpu or '—'}")
        print("  Reading: I/O/wait stages (DB round-trip, LLM call) scale with backend/network,")
        print("  not local CPU — batch/cache/parallelize them; CPU stages scale with local cores.")
    finally:
        try:
            with drv.session(database="system") as s:
                s.run(f"DROP DATABASE `{DB}` IF EXISTS").consume()
            drv.close()
        except Exception:
            pass
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
        print("\nthrowaway DozerDB removed; running stack untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
