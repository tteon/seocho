#!/usr/bin/env python3
"""Detect six planted crime patterns without being told where they are.

Every question in this experiment so far named its own anchor — "account 18503 is under
review" — which hands the detector its answer and makes precision automatic. That measured
recall and called it detection. This asks the question an analyst actually starts from: no
account is under review, find the patterns.

Because nothing is anchored, the numbers that matter change:

* **Recall** is largely scale-insensitive. The planted pattern is still there at SF1000.
* **Precision** is not. The population of innocent look-alikes grows with the graph, and
  that growth is the whole scalability claim in detection terms — a rule that is precise at
  SF1 can be useless at SF1000 without a single line changing.

Two properties of the data make the scores meaningful rather than decorative, and both had
to be fixed before this script could say anything:

**Amount is not evidence.** Planted amounts are drawn from the ordinary interval, and
ordinary traffic carries a heavy tail. Before that, planted transfers ran to ~10M against an
innocent ceiling of 50,010, so `amount > 1000000` isolated every planted edge at 100%
precision — any detection score would have been measuring the giveaway.

**Rank by size and you miss the worst case.** The nominee ring aggregates 12M across 468
small legs, while innocent owners reach hundreds of millions on a handful of large legitimate
transfers. Sorting owners by total put the planted owner 100th of 103 above the threshold. A
detector has to key on structure — accounts per owner, legs per account, spread over time —
not on magnitude.

Each rule below is deliberately the *plausible first attempt*, not a tuned oracle. The point
is to measure how a reasonable rule degrades with scale, so a rule that scores badly is a
result rather than a bug — read the precision column, not the pass/fail.

Usage:
    python scripts/finbench/detection_workload.py --src outputs/finbench/sf1-pat \
        --out outputs/finbench/detection_sf1.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Set, Tuple

LAUNDERING_CHANNELS = ("WIRE_CROSSBORDER", "VIRTUAL_ASSET", "MVTS_HAWALA", "ATM_CD")
CTR_THRESHOLD = 10_000_000
DAY = 86_400
# Bounds the two-path join. Unbounded it exhausts 10 GB on a power-law graph, so cycle counts
# are a lower bound over non-hub nodes and the report says so rather than implying exactness.
OUTDEGREE_CAP = 40


def _q(src: Path, kind: str, name: str) -> str:
    return f"read_parquet('{src}/{kind}/{name}.parquet')"


def _score(found: Set[Any], truth: Set[Any]) -> Dict[str, Any]:
    """Precision and recall, kept separate because they answer different questions.

    Recall says "is the pattern still findable at this scale". Precision says "is the finding
    usable" — and an analyst handed 500 candidates for one real case is not helped, however
    good the recall.
    """
    tp = len(found & truth)
    return {
        "found": len(found),
        "true_positives": tp,
        "false_positives": len(found - truth),
        "missed": len(truth - found),
        "precision": round(tp / len(found), 4) if found else None,
        "recall": round(tp / len(truth), 4) if truth else None,
    }


def detect_nominee_structuring(con, src: Path, gold: Dict[str, Any]) -> Dict[str, Any]:
    """Owners whose accounts collectively receive above the threshold in small pieces.

    The rule keys on structure, not size: many accounts, many legs, every leg below the
    threshold. Ranking by total instead would rank the planted owner near last.
    """
    own, tr = _q(src, "edges", "own"), _q(src, "edges", "transfer")
    # Keyed on the *small-leg subset*, not on all of an owner's traffic. Requiring every leg
    # to be small (`max(amount) <= 60000`) missed the planted ring entirely at SF10 and above:
    # the nominee operator also owns ordinary accounts, those receive heavy-tailed legitimate
    # transfers, and one large innocent leg disqualified the whole owner. Recall went to 0 at
    # three of six grid points — a rule that only works when the suspect does nothing normal.
    small_leg = 60_000
    rows = con.execute(
        f"""
        WITH small AS (
            SELECT o.src AS owner, o.dst AS acct, t.amount
            FROM {own} o JOIN {tr} t ON t.dst = o.dst
            WHERE t.amount <= {small_leg}
        ), per_owner AS (
            SELECT owner,
                   count(DISTINCT acct) AS accounts,
                   count(*) AS legs,
                   sum(amount) AS small_total
            FROM small GROUP BY owner
        )
        SELECT owner FROM per_owner
        WHERE small_total > {CTR_THRESHOLD}   -- the sub-threshold pieces alone add up
          AND accounts >= 5                   -- fragmented across nominees
          AND legs >= 50                      -- assembled from many small pieces
        """
    ).fetchall()
    truth = {gold["answer"]["owner"]}
    return {"rule": "owner-level aggregate of sub-threshold legs, fragmented",
            **_score({int(r[0]) for r in rows}, truth)}


def detect_layering_cycle(con, src: Path, gold: Dict[str, Any]) -> Dict[str, Any]:
    """Three-account cycles whose every hop rides a low-traceability channel."""
    tr = _q(src, "edges", "transfer")
    inlist = ", ".join(f"'{c}'" for c in LAUNDERING_CHANNELS)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE _e AS
        WITH od AS (SELECT src AS v, count(DISTINCT dst) AS o FROM {tr}
                    WHERE src <> dst GROUP BY src)
        SELECT s.* FROM (SELECT DISTINCT src, dst, channel, ts FROM {tr} WHERE src <> dst) s
        JOIN od a ON a.v = s.src AND a.o <= {OUTDEGREE_CAP}
        JOIN od b ON b.v = s.dst AND b.o <= {OUTDEGREE_CAP}
        """
    )
    rows = con.execute(
        f"""
        SELECT DISTINCT list_sort([a.src, b.src, c.src]) AS ring
        FROM _e a JOIN _e b ON b.src = a.dst
        JOIN _e c ON c.src = b.dst AND c.dst = a.src
        WHERE a.channel IN ({inlist}) AND b.channel IN ({inlist})
          AND c.channel IN ({inlist})
          AND greatest(a.ts, b.ts, c.ts) - least(a.ts, b.ts, c.ts) <= {DAY}
        """
    ).fetchall()
    found = {tuple(sorted(int(x) for x in r[0])) for r in rows}
    truth = {tuple(sorted(c)) for c in gold["answer"]["cycles"] if len(c) == 3}
    out = _score(found, truth)
    out["note"] = (f"cycles counted over nodes with out-degree <= {OUTDEGREE_CAP}; a lower "
                   "bound, because the unbounded two-path join exhausts 10 GB here")
    return {"rule": "3-cycle, all hops on high-risk channels, closes within 24h", **out}


def detect_funnel_account(con, src: Path, gold: Dict[str, Any]) -> Dict[str, Any]:
    """Accounts that collect from many senders then move the total on within hours."""
    tr = _q(src, "edges", "transfer")
    rows = con.execute(
        f"""
        WITH inbound AS (
            SELECT dst AS acct, count(DISTINCT src) AS senders,
                   sum(amount) AS collected, max(ts) AS last_in
            FROM {tr} GROUP BY dst
        ), outbound AS (
            SELECT src AS acct, min(ts) AS first_out, max(amount) AS biggest_out
            FROM {tr} GROUP BY src
        )
        SELECT i.acct FROM inbound i JOIN outbound o ON o.acct = i.acct
        WHERE i.senders >= 20
          AND o.first_out > i.last_in - {6 * 3600}
          AND o.biggest_out >= i.collected * 0.5
        """
    ).fetchall()
    truth = {gold["answer"]["hub"]}
    return {"rule": "many senders, onward move of most of the total within hours",
            **_score({int(r[0]) for r in rows}, truth)}


def detect_loan_integration(con, src: Path, gold: Dict[str, Any]) -> Dict[str, Any]:
    """A value path crossing TRANSFER -> REPAY -> DEPOSIT -> TRANSFER."""
    tr, rp, dp = (_q(src, "edges", "transfer"), _q(src, "edges", "repay"),
                  _q(src, "edges", "deposit"))
    rows = con.execute(
        f"""
        SELECT DISTINCT t1.src AS origin
        FROM {tr} t1
        JOIN {rp} r ON r.src = t1.dst
        JOIN {dp} d ON d.src = r.dst
        JOIN {tr} t2 ON t2.src = d.dst
        WHERE t2.dst <> t1.src AND d.dst <> t1.dst
        """
    ).fetchall()
    truth = {gold["answer"]["path"][0]}
    return {"rule": "TRANSFER -> REPAY -> DEPOSIT -> TRANSFER chain",
            **_score({int(r[0]) for r in rows}, truth)}


def detect_common_control(con, src: Path, gold: Dict[str, Any]) -> Dict[str, Any]:
    """Transferring accounts whose owners guarantee each other and share a device."""
    tr, own = _q(src, "edges", "transfer"), _q(src, "edges", "own")
    gt, si = _q(src, "edges", "guarantee"), _q(src, "edges", "sign_in")
    rows = con.execute(
        f"""
        SELECT DISTINCT list_sort([t.src, t.dst]) AS pair
        FROM (SELECT DISTINCT src, dst FROM {tr}) t
        JOIN {own} oa ON oa.dst = t.src
        JOIN {own} ob ON ob.dst = t.dst
        JOIN (SELECT DISTINCT src, dst FROM {gt}) g
          ON (g.src = oa.src AND g.dst = ob.src) OR (g.src = ob.src AND g.dst = oa.src)
        JOIN (SELECT DISTINCT src, dst FROM {si}) sa ON sa.dst = t.src
        JOIN (SELECT DISTINCT src, dst FROM {si}) sb ON sb.dst = t.dst AND sb.src = sa.src
        """
    ).fetchall()
    found = {tuple(sorted(int(x) for x in r[0])) for r in rows}
    truth = {tuple(sorted(gold["answer"]["accounts"]))}
    return {"rule": "transfer + mutually guaranteeing owners + shared login device",
            **_score(found, truth)}


def detect_equity_integration(con, src: Path, gold: Dict[str, Any]) -> Dict[str, Any]:
    """Money into an account whose owner then takes a stake in a company."""
    tr, own, inv = (_q(src, "edges", "transfer"), _q(src, "edges", "own"),
                    _q(src, "edges", "invest"))
    rows = con.execute(
        f"""
        SELECT DISTINCT t.dst AS acct
        FROM {tr} t JOIN {own} o ON o.dst = t.dst
        JOIN {inv} i ON i.src = o.src
        WHERE i.ts > t.ts AND i.ts - t.ts <= {30 * DAY}
        """
    ).fetchall()
    truth = {gold["answer"]["account"]}
    return {"rule": "inbound transfer then an equity stake by the same owner within 30 days",
            **_score({int(r[0]) for r in rows}, truth)}


DETECTORS: Dict[str, Callable] = {
    "nominee_structuring": detect_nominee_structuring,
    "layering_cycle": detect_layering_cycle,
    "funnel_account": detect_funnel_account,
    "loan_integration": detect_loan_integration,
    "common_control": detect_common_control,
    "equity_integration": detect_equity_integration,
}


def main() -> None:
    import duckdb

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--memory-limit", default="10GB")
    parser.add_argument("--only", default="", help="comma-separated detector names")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    with (args.src / "gold.json").open('r', encoding='utf-8') as f:

        gold = json.load(f)
    detection = gold.get("detection")
    if not detection:
        raise SystemExit(
            f"{args.src}/gold.json has no `detection` block — regenerate the snapshot with "
            "the current generator, which publishes the exact answer set and the population "
            "each pattern must be separated from")
    with (args.src / "manifest.json").open('r', encoding='utf-8') as f:
        manifest = json.load(f)

    con = duckdb.connect()
    con.execute(f"SET memory_limit='{args.memory_limit}'")

    wanted = [w.strip() for w in args.only.split(",") if w.strip()] or list(DETECTORS)
    results = []
    for name in wanted:
        fn = DETECTORS.get(name)
        if fn is None or name not in detection:
            print(f"[detect] {name}: no detector or no gold, skipping", flush=True)
            continue
        try:
            out = fn(con, args.src, detection[name])
        except Exception as exc:
            out = {"error": f"{type(exc).__name__}: {exc}"[:200]}
        results.append({"pattern": name, "requires": detection[name].get("requires"), **out})
        p, r = out.get("precision"), out.get("recall")
        print(f"[detect] {name:22s} found={out.get('found', '—'):>8} "
              f"tp={out.get('true_positives', '—'):>4} "
              f"precision={'—' if p is None else format(p, '.4f'):>8} "
              f"recall={'—' if r is None else format(r, '.2f'):>5}", flush=True)

    sp = manifest.get("structural_profile") or {}
    report = {
        "schema_version": "seocho.finbench.detection-workload.v1",
        "source": str(args.src),
        "scale_factor": manifest.get("scale_factor"),
        "structure": {
            "hub_skew": manifest.get("hub_skew"),
            "edges": (sp.get("multiplicity") or {}).get("edges"),
            "max_degree": (manifest.get("degree_profile") or {}).get("max"),
            "avg_local_clustering": (sp.get("clustering") or {}).get("avg_local_clustering"),
            "incidental_3_cycles": (sp.get("motifs") or {}).get("directed_3_cycles"),
        },
        "results": results,
    }

    lines = ["# Detection workload — six planted patterns, nothing anchored", "",
             f"`{args.src}` · SF {manifest.get('scale_factor')} · hub_skew "
             f"{manifest.get('hub_skew')}", "",
             "Recall says the pattern is still findable at this scale. **Precision says the "
             "finding is usable** — an analyst handed hundreds of candidates for one real case "
             "is not helped, however good the recall. Rules are the plausible first attempt, "
             "not tuned oracles, so a low score is a measurement rather than a bug.", "",
             "| pattern | rule keys on | candidates | true positives | precision | recall |",
             "|---|---|---|---|---|---|"]
    for r in results:
        if "error" in r:
            lines.append(f"| {r['pattern']} | — | — | — | — | ({r['error']}) |")
            continue
        p, rc = r.get("precision"), r.get("recall")
        lines.append(
            f"| {r['pattern']} | {r.get('rule', '')} | {r['found']:,} | "
            f"{r['true_positives']} | "
            f"{'—' if p is None else format(p, '.4f')} | "
            f"{'—' if rc is None else format(rc, '.0%')} |")
    markdown = "\n".join(lines) + "\n"

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n")
        args.out.with_suffix(".md").write_text(markdown)
    print(markdown)


if __name__ == "__main__":
    main()
