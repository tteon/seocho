#!/usr/bin/env python3
"""Publish the structural properties a result has to be read against.

Three times over, a conclusion in this experiment turned out to be scoped to a property of
the generated data that was never stated: degree distribution (measured max 31 where
FinBench expects hubs in the millions), edge multiplicity (14 duplicate pairs over 10M
edges, so ``count`` and ``count(DISTINCT)`` are indistinguishable), and now triadic
closure. Each was invisible because the generator took *size* as a parameter and left every
*distribution* to uniform sampling — and because nothing published what the data actually
looked like.

This measures all of it from the Parquet snapshot, so the numbers ship next to the data
rather than being rediscovered when a conclusion breaks. It is the same role FinBench's
"factor tables" play: statistical properties of the generated dataset, stated up front.

Clustering is the one that most changes how the scenarios should be read. Real payment and
social networks have average local clustering around 0.1-0.5; uniform attachment gives
essentially zero, measured here as exactly 0.000 over a 3,000-node sample at SF1000. That
matters for motif work specifically: a graph with no triadic closure contains almost no
incidental cycles, so a cycle-detection question anchored on a known account measures
*recall* and gets *precision* for free. The AML task that actually matters — find the
suspicious rings among the ordinary ones — cannot be posed on a graph whose only rings are
the planted ones.

Two measurements are deliberately bounded rather than exact, and the bound is reported:

* Local clustering is sampled over nodes with degree in [2, cap]. The neighbour-pair join
  is quadratic in degree, so an unbounded version dies on a hub. Hubs are excluded from
  the *sample*, not from the graph, so this measures the clustering an ordinary account
  sits in — which is what a motif traversal would walk.
* Directed 3-cycles are counted over nodes below an out-degree cap, for the same reason:
  the unbounded 2-path join OOMed at 10GB on the hub graph. The share of edges covered is
  reported so the count can be read as the near-complete lower bound it is.

Usage:
    python scripts/finbench/graph_properties.py --src outputs/finbench/sf1000-hub
    python scripts/finbench/graph_properties.py --src outputs/finbench/sf1000 --update-manifest
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

SAMPLE_NODES = 3000
DEGREE_CAP = 200
OUTDEGREE_CAP = 40
# Reference band for real-world networks, for reading the measured value against.
REAL_WORLD_CLUSTERING = (0.1, 0.5)


def measure(src: Path, *, sample_nodes: int, degree_cap: int,
            outdegree_cap: int) -> Dict[str, Any]:
    import duckdb

    con = duckdb.connect()
    con.execute("SET memory_limit='10GB'")
    transfer = str(src / "edges" / "transfer.parquet")
    con.execute(f"CREATE VIEW t AS SELECT * FROM '{transfer}'")

    # ---- multiplicity, on the raw edge list ----
    edges, pairs, max_mult, dup_pairs, dup_edges = con.execute(
        """
        WITH p AS (SELECT src, dst, count(*) AS m FROM t GROUP BY src, dst)
        SELECT (SELECT count(*) FROM t), count(*), max(m),
               sum(CASE WHEN m > 1 THEN 1 ELSE 0 END),
               sum(CASE WHEN m > 1 THEN m ELSE 0 END)
        FROM p
        """
    ).fetchone()

    # ---- simple undirected view: structure without repetition or self-loops ----
    con.execute("""CREATE TABLE u AS SELECT DISTINCT least(src,dst) AS a,
                   greatest(src,dst) AS b FROM t WHERE src <> dst""")
    con.execute("CREATE TABLE sym AS SELECT a AS v, b AS w FROM u UNION ALL SELECT b, a FROM u")
    con.execute("CREATE TABLE deg AS SELECT v, count(*) AS d FROM sym GROUP BY v")
    n_nodes, n_simple = con.execute(
        "SELECT (SELECT count(*) FROM deg), (SELECT count(*) FROM u)").fetchone()

    # ---- sampled local clustering ----
    con.execute(f"""CREATE TABLE samp AS SELECT v, d FROM deg
                    WHERE d BETWEEN 2 AND {degree_cap} USING SAMPLE {sample_nodes} ROWS""")
    con.execute("""CREATE TABLE nb AS SELECT s.v AS v, sym.w AS w, s.d AS d
                   FROM samp s JOIN sym ON sym.v = s.v""")
    sampled, avg_cc, tri_total, nodes_with_tri = con.execute(
        """
        WITH pairs AS (
            SELECT n1.v AS v, n1.d AS d, n1.w AS x, n2.w AS y
            FROM nb n1 JOIN nb n2 ON n1.v = n2.v AND n1.w < n2.w
        ), closed AS (
            SELECT p.v, count(*) AS tri FROM pairs p
            JOIN u ON u.a = least(p.x,p.y) AND u.b = greatest(p.x,p.y)
            GROUP BY p.v
        ), per AS (
            SELECT s.v, s.d, coalesce(cl.tri,0) AS tri,
                   coalesce(cl.tri,0) / (s.d*(s.d-1)/2.0) AS cc
            FROM samp s LEFT JOIN closed cl ON cl.v = s.v
        )
        SELECT count(*), avg(cc), sum(tri), sum(CASE WHEN tri>0 THEN 1 ELSE 0 END) FROM per
        """
    ).fetchone()

    # ---- bounded directed 3-cycle count ----
    con.execute(f"""CREATE TABLE e2 AS
      WITH od AS (SELECT src AS v, count(DISTINCT dst) AS o FROM t WHERE src<>dst GROUP BY src)
      SELECT DISTINCT s.src, s.dst
      FROM (SELECT DISTINCT src,dst FROM t WHERE src<>dst) s
      JOIN od a ON a.v=s.src AND a.o<={outdegree_cap}
      JOIN od b ON b.v=s.dst AND b.o<={outdegree_cap}""")
    covered = con.execute("SELECT count(*) FROM e2").fetchone()[0]
    triples = con.execute(
        """SELECT count(*) FROM e2 a JOIN e2 b ON b.src=a.dst
           JOIN e2 c ON c.src=b.dst AND c.dst=a.src"""
    ).fetchone()[0]

    avg_cc = float(avg_cc or 0.0)
    return {
        "nodes_with_edges": int(n_nodes),
        "multiplicity": {
            "edges": int(edges),
            "distinct_pairs": int(pairs),
            "redundancy": round(edges / pairs, 4) if pairs else None,
            "max_multiplicity": int(max_mult),
            "duplicate_pairs": int(dup_pairs or 0),
            "pct_edges_in_duplicate_pairs": round(100.0 * (dup_edges or 0) / edges, 3),
            # Stated because it decides whether a semantic error is detectable at all:
            # at 1.0 the two questions have the same answer.
            "distinct_vs_total_distinguishable": (edges / pairs) > 1.01 if pairs else False,
        },
        "clustering": {
            "simple_undirected_edges": int(n_simple),
            "sampled_nodes": int(sampled),
            "degree_range_sampled": [2, degree_cap],
            "avg_local_clustering": avg_cc,
            "triangles_touched": int(tri_total or 0),
            "nodes_in_any_triangle": int(nodes_with_tri or 0),
            "pct_sampled_in_any_triangle": (
                round(100.0 * (nodes_with_tri or 0) / sampled, 2) if sampled else None),
            "real_world_reference": list(REAL_WORLD_CLUSTERING),
            "shortfall_vs_real_world_low_end": (
                round(REAL_WORLD_CLUSTERING[0] / avg_cc, 1) if avg_cc > 0 else "infinite"),
        },
        "motifs": {
            "directed_3_cycles": int(triples // 3),
            "outdegree_cap": outdegree_cap,
            "edges_covered": int(covered),
            "pct_edges_covered": round(100.0 * covered / n_simple, 2) if n_simple else None,
            "bound": "lower bound — nodes above the out-degree cap are excluded because "
                     "the unbounded 2-path join exhausts 10GB on a power-law graph",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--sample-nodes", type=int, default=SAMPLE_NODES)
    parser.add_argument("--degree-cap", type=int, default=DEGREE_CAP)
    parser.add_argument("--outdegree-cap", type=int, default=OUTDEGREE_CAP)
    parser.add_argument("--update-manifest", action="store_true",
                        help="merge the profile into <src>/manifest.json under "
                             "structural_profile, so the data ships with its properties")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    profile = measure(args.src, sample_nodes=args.sample_nodes,
                      degree_cap=args.degree_cap, outdegree_cap=args.outdegree_cap)

    m = profile["multiplicity"]
    cl = profile["clustering"]
    mo = profile["motifs"]
    print(f"{args.src}")
    print(f"  nodes with edges     {profile['nodes_with_edges']:,}")
    print(f"  edges                {m['edges']:,}  distinct pairs {m['distinct_pairs']:,}")
    print(f"  redundancy           {m['redundancy']}x  max multiplicity {m['max_multiplicity']}")
    print(f"  DISTINCT detectable  {m['distinct_vs_total_distinguishable']}")
    print(f"  avg local clustering {cl['avg_local_clustering']:.3e}  "
          f"({cl['pct_sampled_in_any_triangle']}% of {cl['sampled_nodes']} sampled nodes "
          f"in any triangle)")
    print(f"  vs real world        {cl['real_world_reference']} -> short by "
          f"{cl['shortfall_vs_real_world_low_end']}x")
    print(f"  directed 3-cycles    {mo['directed_3_cycles']:,} "
          f"(lower bound, {mo['pct_edges_covered']}% of edges covered)")

    if args.update_manifest:
        path = args.src / "manifest.json"
        with path.open('r', encoding='utf-8') as f:
            manifest = json.load(f)
        manifest["structural_profile"] = profile
        path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"  -> merged into {path} under structural_profile")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"source": str(args.src), **profile}, indent=2) + "\n")


if __name__ == "__main__":
    main()
