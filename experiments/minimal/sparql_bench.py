#!/usr/bin/env python3
"""Is Oxigraph worth adopting for competency questions, measured rather than assumed.

Competency questions are only tests if they execute. Ours do not: they are prose
with a bespoke Python or Cypher measurement each. The standard way to fix that
is to express each one as SPARQL and run it against the ontology plus extracted
data, which needs a SPARQL engine this project does not have.

Two candidates, and the question is whether the Rust one is a burden:

    rdflib      already a dependency, pure Python, SPARQL 1.1
    pyoxigraph  Rust, PyO3 bindings, SPARQL 1.1, an actual store

CLAUDE.md 21 forbids choosing a native component on intuition, and 21.4 says
adopt an existing Rust-backed library before writing one. This measures the
whole path a caller pays — load plus query — because load is the part a
reasoning or CQ loop repeats, and an inner-query ratio alone is not evidence.

Queries are the shapes competency questions actually take: a lookup, a
hierarchy walk (which needs transitive closure and is where a triple store
usually separates from a naive one), and a count.

No model is called and nothing is written to Neo4j.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for path in (str(HERE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

TTL = ROOT / "dataset/fibo/fibo-quickstart.ttl"
OUT_ROOT = ROOT / "outputs/minimal"

QUERIES = {
    "lookup_labels": """
        SELECT ?c ?l WHERE {
          ?c a <http://www.w3.org/2002/07/owl#Class> ;
             <http://www.w3.org/2000/01/rdf-schema#label> ?l .
        } LIMIT 200
    """,
    "hierarchy_closure": """
        SELECT ?sub WHERE {
          ?sub <http://www.w3.org/2000/01/rdf-schema#subClassOf>+ ?super .
          ?super <http://www.w3.org/2000/01/rdf-schema#label> "contract" .
        }
    """,
    "synonym_bearing_classes": """
        SELECT ?c ?syn WHERE {
          ?c <https://www.omg.org/spec/Commons/AnnotationVocabulary/synonym> ?syn .
        }
    """,
    "count_object_properties": """
        SELECT (COUNT(DISTINCT ?p) AS ?n) WHERE {
          ?p a <http://www.w3.org/2002/07/owl#ObjectProperty> .
        }
    """,
}


def timed(fn: Callable[[], Any]) -> tuple[float, Any]:
    started = time.perf_counter()
    value = fn()
    return round(time.perf_counter() - started, 4), value


def main() -> int:
    import observe

    run = observe.Run(OUT_ROOT, "sparql-bench", {"decisive": {
        "source": str(TTL.relative_to(ROOT)),
        "queries": sorted(QUERIES), "seed": 42}})

    results: dict[str, dict[str, Any]] = {}

    with run.stage("rdflib.load") as out:
        from rdflib import Graph

        seconds, graph = timed(lambda: _rdflib_load())
        out["seconds"] = seconds
        out["triples"] = len(graph)
        results["rdflib"] = {"load_seconds": seconds, "triples": len(graph),
                             "queries": {}}

    for name, query in QUERIES.items():
        with run.stage(f"rdflib.query.{name}") as out:
            try:
                seconds, rows = timed(lambda q=query: list(graph.query(q)))
                out["seconds"] = seconds
                out["rows"] = len(rows)
                results["rdflib"]["queries"][name] = {"seconds": seconds,
                                                      "rows": len(rows)}
            except Exception as exc:  # noqa: BLE001 — recorded, never imputed
                out["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                results["rdflib"]["queries"][name] = {"error": out["error"]}

    with run.stage("oxigraph.load") as out:
        import pyoxigraph

        def load_ox():
            store = pyoxigraph.Store()
            with TTL.open("rb") as fh:
                store.load(fh, format=pyoxigraph.RdfFormat.TURTLE)
            return store

        seconds, store = timed(load_ox)
        out["seconds"] = seconds
        out["triples"] = len(store)
        results["oxigraph"] = {"load_seconds": seconds, "triples": len(store),
                               "queries": {}}

    for name, query in QUERIES.items():
        with run.stage(f"oxigraph.query.{name}") as out:
            try:
                seconds, rows = timed(lambda q=query: list(store.query(q)))
                out["seconds"] = seconds
                out["rows"] = len(rows)
                results["oxigraph"]["queries"][name] = {"seconds": seconds,
                                                        "rows": len(rows)}
            except Exception as exc:  # noqa: BLE001
                out["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                results["oxigraph"]["queries"][name] = {"error": out["error"]}

    # The whole path a competency-question suite pays: load once, run every
    # question. That is the number the decision turns on, not any single query.
    suite: dict[str, float] = {}
    for engine, cell in results.items():
        query_total = sum(q.get("seconds", 0.0)
                          for q in cell["queries"].values())
        suite[engine] = round(cell["load_seconds"] + query_total, 4)

    faster = min(suite, key=suite.get)
    ratio = (round(max(suite.values()) / min(suite.values()), 2)
             if min(suite.values()) > 0 else 0.0)

    payload = {
        "contract": "log2026.sparql_bench.v1",
        "question": ("For running competency questions as SPARQL, is the Rust "
                     "store worth adopting over the rdflib already present?"),
        "method": ("whole path per engine: parse the FIBO turtle, then run the "
                   "four query shapes a competency-question suite uses; the "
                   "reported total is load plus all queries (CLAUDE.md 21.1)"),
        "claim_boundary": ("One ontology, one machine, single run, no warm "
                           "cache control. Enough to tell an order of magnitude "
                           "from a wash, not to rank engines."),
        "engines": results,
        "suite_seconds": suite,
        "faster": faster, "ratio": ratio,
    }
    (run.dir / "sparql_bench.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print()
    print(f"{'':26s} {'rdflib':>12s} {'oxigraph':>12s}")
    print(f"{'load':26s} {results['rdflib']['load_seconds']:12.3f} "
          f"{results['oxigraph']['load_seconds']:12.3f}")
    for name in QUERIES:
        r = results["rdflib"]["queries"][name]
        o = results["oxigraph"]["queries"][name]
        print(f"{name:26s} {r.get('seconds', float('nan')):12.3f} "
              f"{o.get('seconds', float('nan')):12.3f}   "
              f"rows {r.get('rows', '-')}/{o.get('rows', '-')}")
    print(f"{'SUITE (load + queries)':26s} {suite['rdflib']:12.3f} "
          f"{suite['oxigraph']:12.3f}")
    print(f"\n{faster} is {ratio}x faster on the whole path")

    run.finish({"suite_seconds": suite, "faster": faster, "ratio": ratio,
                "artifact": str((run.dir / "sparql_bench.json").relative_to(ROOT))})
    return 0


def _rdflib_load():
    from rdflib import Graph

    graph = Graph()
    graph.parse(TTL, format="turtle")
    return graph


if __name__ == "__main__":
    raise SystemExit(main())
