#!/usr/bin/env python3
"""Choose query anchors by what they cost, not by what they look like.

The degree-band probe produced a result that invalidates the obvious way of picking
benchmark anchors. Running the same 2-hop query from anchors selected by degree gave:

    median  degree      6  ->    158,487 db hits
    p99     degree     73  ->      3,876 db hits
    p99.9   degree    336  ->    429,042 db hits
    hub     degree 158,315 ->    timeout

Not monotonic, and off by a factor of forty between the two smallest. Preferential
attachment is why: edges are sampled in proportion to degree, so a low-degree node's few
neighbours are disproportionately likely to *be* hubs. **Cost follows the neighbourhood,
not the anchor.** Any anchor-local property — degree, risk tier, label — is therefore the
wrong key, and a sweep whose anchors are chosen that way cannot separate "this profile is
slower" from "this anchor was harder".

LDBC hit this first and answered it with parameter curation: select bindings whose
*intermediate result sizes* are similar at every level of the intended plan, so runtimes
are comparable despite skew (Gubichev & Boncz, "Parameter Curation for Benchmark
Queries", TPCTC 2014). This is that idea at the scope this experiment needs.

For a 2-hop expansion from account ``a`` the intended plan has two levels:

    L1 = |{b : a -> b}|                      the first expansion
    L2 = sum over those b of |{c : b -> c}|  the second, and the real cost driver

Both are computed offline in DuckDB against the Parquet snapshot — no database round
trips, no dependence on the engine whose cost we are trying to control for. Candidates
are then grouped into target bands by L2 and, within each band, the K anchors closest to
the band's median L2 are taken, which minimises spread by construction.

The output records the achieved coefficient of variation for each band so a reader can
see how tight the curation actually is rather than trusting that it worked, and
``--validate`` measures real db hits per anchor to check the offline estimate predicts
engine cost at all.

Usage:
    python scripts/finbench/curate_parameters.py --src outputs/finbench/sf1000-hub
    python scripts/finbench/curate_parameters.py --src outputs/finbench/sf1000-hub \
        --validate --database finbenchsf1000hub --password "$NEO4J_PASSWORD"
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any, Dict, List

# Bands are quantiles of L2 — the second-level intermediate result size. Naming them by
# what they mean for a run (does this anchor's work fit in cache? does it finish?) is
# more useful than naming them by percentile.
DEFAULT_BANDS: List[tuple[str, float]] = [
    ("tiny", 0.10),
    ("small", 0.50),
    ("medium", 0.90),
    ("large", 0.99),
    ("huge", 0.999),
    # The extreme, not a percentile. The 99.9th percentile sat at L2 192,942 while the
    # actual maximum was 51,447,907 — 266x higher — so a sweep that stops at "huge" never
    # touches the case a cost gate most needs to get right.
    ("max", 1.0),
]


def compute_levels(con, src: Path, sample: int | None) -> None:
    """Materialise per-account intermediate result sizes for the 2-hop plan."""
    transfer = str(src / "edges" / "transfer.parquet")
    con.execute(f"CREATE OR REPLACE VIEW t AS SELECT * FROM '{transfer}'")
    # Out-degree per node: level-1 size, and the per-neighbour multiplier for level 2.
    con.execute(
        "CREATE OR REPLACE TABLE outdeg AS "
        "SELECT src AS id, count(*) AS deg FROM t GROUP BY src"
    )
    # L2 is a sum over the *edges* out of a, of the out-degree of each target. Summing
    # over edges rather than distinct neighbours is deliberate: the engine expands once
    # per edge, so edge multiplicity is real work.
    limit = f"USING SAMPLE {sample} ROWS" if sample else ""
    con.execute(
        f"""
        CREATE OR REPLACE TABLE levels AS
        SELECT t.src AS id,
               count(*) AS l1,
               sum(coalesce(o.deg, 0)) AS l2
        FROM t LEFT JOIN outdeg o ON o.id = t.dst
        GROUP BY t.src
        {limit}
        """
    )


def curate(con, bands, per_band: int, key: str = "l2") -> List[Dict[str, Any]]:
    """Pick, per band, the anchors whose ``key`` sits closest to that band's target.

    ``key='l2'`` is the curated method. ``key='l1'`` reproduces the intuitive scheme —
    group by the anchor's own degree — and exists so the comparison is measured rather
    than asserted: both arms use identical band structure and identical selection logic,
    differing only in which quantity is held constant.
    """
    out: List[Dict[str, Any]] = []
    for name, q in bands:
        target = con.execute(
            f"SELECT quantile_cont({key}, ?) FROM levels", [q]
        ).fetchone()[0]
        if target is None:
            continue
        rows = con.execute(
            f"""
            SELECT id, l1, l2 FROM levels
            ORDER BY abs({key} - ?), id
            LIMIT ?
            """,
            [target, per_band],
        ).fetchall()
        if not rows:
            continue
        keyed = [float(r[2] if key == "l2" else r[1]) for r in rows]
        l2s = [float(r[2]) for r in rows]
        mean_l2 = statistics.mean(l2s)
        mean_key = statistics.mean(keyed)
        out.append({
            "band": name,
            "key": key,
            "quantile": q,
            "target_key": round(float(target), 1),
            "key_cv": round(statistics.pstdev(keyed) / mean_key, 4) if mean_key else None,
            "target_l2": round(float(target), 1),
            "achieved_mean_l2": round(mean_l2, 1),
            # Coefficient of variation of the *estimate*. Near zero means the curation
            # succeeded at what it set out to do; it does not by itself prove the
            # estimate predicts engine cost — that is what --validate is for.
            "l2_cv": round(statistics.pstdev(l2s) / mean_l2, 4) if mean_l2 else None,
            "anchors": [
                {"account_id": int(i), "l1": int(a), "l2": int(b)} for i, a, b in rows
            ],
        })
    return out


def validate(bands: List[Dict[str, Any]], *, uri: str, user: str, password: str,
             database: str, timeout_s: float) -> None:
    """Measure real db hits per anchor and record whether L2 predicted them.

    The point of curation is comparable runtimes, so the test is not "is L2 correct" but
    "do anchors curated together actually cost the same". Reported as the CV of measured
    db hits within each band, directly comparable to the CV of the estimate.
    """
    from neo4j import GraphDatabase
    from neo4j.exceptions import Neo4jError

    cypher = (
        "MATCH (a:Account {id: $id})-[:TRANSFER]->(:Account)-[:TRANSFER]->(c:Account) "
        "RETURN count(DISTINCT c) AS n"
    )

    def hits(plan: Dict[str, Any]) -> int:
        return int(plan.get("dbHits", 0) or 0) + sum(
            hits(c) for c in plan.get("children", []) or [])

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        for band in bands:
            measured: List[int] = []
            for anchor in band["anchors"]:
                with driver.session(database=database) as session:
                    tx = session.begin_transaction(timeout=timeout_s)
                    try:
                        result = tx.run("PROFILE " + cypher,
                                        id=f"Account:{anchor['account_id']}")
                        list(result)
                        summary = result.consume()
                        tx.commit()
                        anchor["db_hits"] = hits(summary.profile or {})
                        measured.append(anchor["db_hits"])
                    except Neo4jError as exc:
                        tx.close()
                        anchor["db_hits"] = None
                        anchor["error"] = exc.code
            if measured:
                mean = statistics.mean(measured)
                band["measured_mean_db_hits"] = round(mean, 1)
                band["measured_db_hits_cv"] = (
                    round(statistics.pstdev(measured) / mean, 4) if mean else None)
                band["measured_count"] = len(measured)
            band["timed_out"] = sum(
                1 for a in band["anchors"] if a.get("db_hits") is None)
            print(f"[curate] {band['band']:7s} est L2={band['achieved_mean_l2']:>12,.0f} "
                  f"(cv {band['l2_cv']}) -> measured hits="
                  f"{band.get('measured_mean_db_hits', float('nan')):>12,.0f} "
                  f"(cv {band.get('measured_db_hits_cv')}) "
                  f"timeouts={band['timed_out']}", flush=True)
    finally:
        driver.close()


def main() -> None:
    import duckdb

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, required=True,
                        help="snapshot directory containing edges/transfer.parquet")
    parser.add_argument("--per-band", type=int, default=5,
                        help="anchors to curate per band")
    parser.add_argument("--sample", type=int, default=None,
                        help="sample N candidate sources instead of scanning all")
    parser.add_argument("--baseline-degree", action="store_true",
                        help="also curate by anchor degree (L1) using the same bands and "
                             "the same selection logic, so the two keys can be compared "
                             "on measured spread instead of argued about")
    parser.add_argument("--validate", action="store_true",
                        help="measure real db hits per anchor against a loaded database")
    parser.add_argument("--database")
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="")
    parser.add_argument("--query-timeout", type=float, default=30.0)
    parser.add_argument("--out", type=Path, default=None,
                        help="defaults to <src>/curated_parameters.json")
    args = parser.parse_args()
    if args.validate and not args.database:
        raise SystemExit("--validate requires --database")

    con = duckdb.connect()
    compute_levels(con, args.src, args.sample)
    bands = curate(con, DEFAULT_BANDS, args.per_band, key="l2")
    degree_bands = (curate(con, DEFAULT_BANDS, args.per_band, key="l1")
                    if args.baseline_degree else [])
    stats = con.execute(
        "SELECT count(*), avg(l1), max(l1), avg(l2), max(l2) FROM levels").fetchone()

    if args.validate:
        print("[curate] key=l2 (curated)")
        validate(bands, uri=args.uri, user=args.user, password=args.password,
                 database=args.database, timeout_s=args.query_timeout)
        if degree_bands:
            print("[curate] key=l1 (degree baseline)")
            validate(degree_bands, uri=args.uri, user=args.user, password=args.password,
                     database=args.database, timeout_s=args.query_timeout)

    report = {
        "schema_version": "seocho.finbench.curated-parameters.v1",
        "source": str(args.src),
        "method": "LDBC-style parameter curation on 2-hop intermediate result sizes "
                  "(L1 = first expansion, L2 = second); anchors per band are the ones "
                  "closest to the band's L2 quantile",
        "reference": "Gubichev & Boncz, Parameter Curation for Benchmark Queries, "
                     "TPCTC 2014",
        "population": {
            "sources": stats[0],
            "mean_l1": round(float(stats[1]), 2),
            "max_l1": int(stats[2]),
            "mean_l2": round(float(stats[3]), 2),
            "max_l2": int(stats[4]),
        },
        "per_band": args.per_band,
        "validated_against": args.database if args.validate else None,
        "bands": bands,
        "degree_baseline_bands": degree_bands,
    }
    out = args.out or (args.src / "curated_parameters.json")
    out.write_text(json.dumps(report, indent=2) + "\n")

    lines = [
        "# Curated query parameters", "",
        f"`{args.src}` · {stats[0]:,} source accounts · "
        f"mean L1 {stats[1]:.1f} / mean L2 {stats[3]:,.0f} / max L2 {int(stats[4]):,}", "",
        "Anchors are selected by intermediate result size, not by degree — degree was "
        "measured to be non-monotonic in cost. `l2_cv` is the spread of the offline "
        "estimate; `measured cv` is the spread of real db hits over the same anchors, "
        "and is the number that says whether curation worked.", "",
        "| band | quantile | mean L1 | mean L2 | est. cv | measured hits | measured cv | timeouts |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for b in bands:
        mean_l1 = statistics.mean([a["l1"] for a in b["anchors"]])
        mh = b.get("measured_mean_db_hits")
        lines.append(
            f"| {b['band']} | {b['quantile']} | {mean_l1:,.0f} | "
            f"{b['achieved_mean_l2']:,.0f} | {b['l2_cv']} | "
            f"{'—' if mh is None else format(mh, ',.0f')} | "
            f"{b.get('measured_db_hits_cv', '—')} | {b.get('timed_out', '—')} |")
    if degree_bands:
        lines += [
            "", "## Baseline: the same bands keyed on anchor degree instead", "",
            "Identical band structure and selection logic; only the quantity held "
            "constant differs. If degree were a reasonable key, its measured cv would be "
            "comparable.", "",
            "| band | mean L1 | mean L2 | degree cv | measured hits | measured cv | timeouts |",
            "|---|---|---|---|---|---|---|",
        ]
        for b in degree_bands:
            mh = b.get("measured_mean_db_hits")
            lines.append(
                f"| {b['band']} | {statistics.mean([a['l1'] for a in b['anchors']]):,.0f} | "
                f"{b['achieved_mean_l2']:,.0f} | {b['key_cv']} | "
                f"{'—' if mh is None else format(mh, ',.0f')} | "
                f"{b.get('measured_db_hits_cv', '—')} | {b.get('timed_out', '—')} |")
    markdown = "\n".join(lines) + "\n"
    out.with_suffix(".md").write_text(markdown)
    print(markdown)


if __name__ == "__main__":
    main()
