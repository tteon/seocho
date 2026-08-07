#!/usr/bin/env python3
"""Build the one question in this experiment that has to discriminate, not just retrieve.

Every other case is anchored: the analyst already knows which account is under review, so an
index seek finds it and precision comes free — there are no wrong candidates to return. That
made the existing `laundering_cycle` scenario a recall test wearing a detection test's clothes,
because on a uniform graph the planted ring was the *only* ring: 47 incidental 3-cycles in the
whole graph, none of them competing.

With triadic and cyclic closure the same graph carries **694,699** 3-cycles, and the question an
analyst actually asks becomes askable — *which* of these is suspicious, with no account named up
front. That is a precision measurement, and it is the first case here where returning a superset
is a real failure rather than a scoring technicality.

Gold is computed from the snapshot, and the predicate is narrowed until it is scoreable:

    all 3-cycles                                    694,699
    + every hop on a high-risk channel               13,619   (1.96% — 50 distractors each)
    + whole cycle closes within 24 hours                  9   (1 per 77,189)

**One predicate that looks discriminating is not**, and it is recorded here rather than quietly
dropped: requiring every amount below the CTR reporting threshold changed nothing (13,619 →
13,619), because this generator draws base amounts from `10 + random()*50000` and they are
therefore always sub-threshold. On real data that clause carries weight; on this data it is
decoration, and a benchmark that shipped it as a filter would be claiming a discrimination it
never performed.

Usage:
    python scripts/finbench/ring_case.py --src outputs/finbench/sf1000-real \
        --out examples/finbench/cases_ring.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

# Channels a laundering ring prefers: low traceability, high reach. Same list the generator
# uses when it closes a cycle, so the planted and the incidental rings are drawn from one
# vocabulary rather than the gold being findable by a tell.
LAUNDERING_CHANNELS = ("WIRE_CROSSBORDER", "VIRTUAL_ASSET", "MVTS_HAWALA", "ATM_CD")
WINDOW_HOURS = 24
# Bounds the two-path join. The unbounded version exhausts a 10 GB limit on a power-law graph,
# so the count is a lower bound over non-hub nodes and says so.
OUTDEGREE_CAP = 40


def build(src: Path) -> Dict[str, Any]:
    import duckdb

    con = duckdb.connect()
    con.execute("SET memory_limit='10GB'")
    transfer = str(src / "edges" / "transfer.parquet")
    inlist = ", ".join(f"'{c}'" for c in LAUNDERING_CHANNELS)
    window = WINDOW_HOURS * 3600

    con.execute(
        f"""
        CREATE TABLE e AS
        WITH od AS (SELECT src AS v, count(DISTINCT dst) AS o
                    FROM '{transfer}' WHERE src <> dst GROUP BY src)
        SELECT s.* FROM (SELECT DISTINCT src, dst, channel, amount, ts
                         FROM '{transfer}' WHERE src <> dst) s
        JOIN od a ON a.v = s.src AND a.o <= {OUTDEGREE_CAP}
        JOIN od b ON b.v = s.dst AND b.o <= {OUTDEGREE_CAP}
        """
    )

    def rings(extra: str) -> int:
        return int(con.execute(
            f"""SELECT count(*)/3 FROM e a JOIN e b ON b.src = a.dst
                JOIN e c ON c.src = b.dst AND c.dst = a.src WHERE {extra}"""
        ).fetchone()[0])

    risky = (f"a.channel IN ({inlist}) AND b.channel IN ({inlist}) "
             f"AND c.channel IN ({inlist})")
    timed = (f"{risky} AND greatest(a.ts,b.ts,c.ts) - least(a.ts,b.ts,c.ts) <= {window}")

    funnel = {
        "all_3_cycles": rings("TRUE"),
        "high_risk_channels_only": rings(risky),
        "and_within_window": rings(timed),
    }

    rows = con.execute(
        f"""
        SELECT DISTINCT list_sort([a.src, b.src, c.src]) AS ring,
               greatest(a.ts,b.ts,c.ts) - least(a.ts,b.ts,c.ts) AS span_s
        FROM e a JOIN e b ON b.src = a.dst
        JOIN e c ON c.src = b.dst AND c.dst = a.src
        WHERE {timed}
        ORDER BY ring
        """
    ).fetchall()
    ring_list = [{"accounts": [int(x) for x in r[0]],
                  "span_hours": round(float(r[1]) / 3600, 1)} for r in rows]
    members = sorted({a for r in ring_list for a in r["accounts"]})

    total = funnel["all_3_cycles"]
    kept = funnel["and_within_window"]
    return {
        "schema_version": "seocho.finbench.cases.ring.v1",
        "curated_from": str(src),
        "note": (
            "The only unanchored question in the set, and therefore the only one where "
            "precision is not free. Gold is the account set of every 3-cycle whose hops all "
            "ride high-risk channels and which closes inside "
            f"{WINDOW_HOURS} hours: {kept} rings out of {total:,} in the graph, so returning "
            f"a superset costs real precision. Counts are a lower bound over nodes with "
            f"out-degree <= {OUTDEGREE_CAP}, because the unbounded two-path join exhausts "
            "10 GB on this graph."),
        "selectivity_funnel": funnel,
        "distractors_per_true_positive": (round((total - kept) / kept) if kept else None),
        "vacuous_predicate_note": (
            "Requiring every amount below the CTR threshold was tested and left the count "
            "unchanged (13,619 -> 13,619): this generator's base amounts are always "
            "sub-threshold. Shipping it as a filter would claim a discrimination it does not "
            "perform."),
        "rings": ring_list,
        "cases": [{
            "id": "unanchored_laundering_rings",
            "category": "detection",
            "reasoning_type": "unanchored_motif",
            "question": (
                "Compliance has asked for a proactive sweep — no specific account is under "
                "review. Which accounts are part of a three-account transfer cycle where every "
                f"hop used a high-risk channel and the whole cycle closed within {WINDOW_HOURS} "
                "hours? List the account numbers."),
            "gold": [str(a) for a in members],
            "scenario": "unanchored ring detection against incidental cycles",
            "typology": "Layering — rapid circular flow over low-traceability rails",
            "source": ("FATF layering typology (funds returning to origin have no economic "
                       "purpose) combined with FATF Professional Money Laundering (2018) on "
                       "MVTS/hawala and virtual-asset rails; FFIEC BSA/AML red flags on "
                       "rapid movement"),
            "why_it_matters": (
                "Every other question in this set names the account to investigate, which hands "
                "the agent its anchor and makes precision automatic. A proactive sweep does "
                "not: the answer has to be separated from "
                f"{total:,} structurally identical cycles. This is the question a graph is "
                "supposed to be good at, and the first one here that can actually be scored on "
                "precision rather than recall."),
            "difficulty": {
                "cost_band": "unbounded",
                "anchor_kind": "none",
                "terminable": False,
                "answer_size": len(members),
                "answer_fits_row_cap": len(members) <= 50,
                "distractor_density": "high",
                "distractors_per_true_positive": (round((total - kept) / kept)
                                                 if kept else None),
                "direction_ambiguous": True,
                "edge_types_traversed": 1,
            },
        }],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    doc = build(args.src)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2) + "\n")

    f = doc["selectivity_funnel"]
    print(f"  all 3-cycles                     {f['all_3_cycles']:>10,}")
    print(f"  + every hop high-risk channel    {f['high_risk_channels_only']:>10,}")
    print(f"  + closes within {WINDOW_HOURS}h            "
          f"{f['and_within_window']:>10,}")
    print(f"  distractors per true positive    "
          f"{doc['distractors_per_true_positive']:>10,}")
    print(f"  gold accounts                    {len(doc['cases'][0]['gold']):>10,}")
    print(f"  -> {args.out}")


if __name__ == "__main__":
    main()
