#!/usr/bin/env python3
"""Build agent cases that actually touch a hub, with exact gold from the snapshot.

Running the existing case set against the power-law graph gave 100% (9/9), and that
number means nothing about hubs. Every anchor in `cases.json` is a *planted* account —
the AML typologies live at reserved ids outside the random attachment range — so their
degrees measured 2 to 26 against a graph whose maximum is 158,315. The questions never go
near a hub, so the graph's degree distribution cannot influence the result either way.

That is worth stating as a benchmark-design point in its own right: planting patterns at
reserved ids makes gold answers exact, and simultaneously makes the case set blind to
whatever the rest of the graph looks like. Precision about the answer bought ignorance
about the environment.

This generates cases anchored on the *curated* anchors instead, one per band, so the
question set spans stated working-set sizes from small to hub. Gold is computed in DuckDB
over the same Parquet the graph was loaded from, which keeps it independent of the engine
being tested and exact rather than sampled.

Three question shapes per anchor, chosen because the degree probe showed the shape decides
whether a hub matters at all:

``fan_out_count``   1-hop aggregate — cheap everywhere, the control.
``twohop_count``    2-hop aggregate — no early exit; this is the shape that timed out on
                    the hub, so it is where retrieval should break if it breaks.
``fan_out_list``    1-hop list, early-terminable, so it should survive a hub. Emitted only
                    when the anchor's whole fan-out fits the cap, so the expected answer is
                    a *set* and scoring cannot depend on ordering. A capped list from a
                    larger fan-out is not scoreable here: gold would have to fix an order,
                    and the engine orders by a string display expression (so "1027815"
                    sorts before "46"). Matching gold to that would test the
                    implementation against itself.

Usage:
    python scripts/finbench/hub_cases.py --src outputs/finbench/sf1000-hub \
        --out examples/finbench/cases_hub.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

# Question templates in the language an analyst would actually use, with the investigative
# context that makes each one worth asking. The earlier phrasings ("how many distinct accounts
# are reachable within 2 transfer hops from account 18503") isolated the cost axes correctly
# and resembled nobody's real work — and that matters beyond presentation, because a question
# nobody would ask is a question whose failure nobody can judge the cost of. The measured
# difficulty vector is unchanged; only the framing is.
#
# Each carries typology and source so the question is defensible rather than invented, matching
# the convention already established in examples/finbench/cases_aml.json.
TEMPLATES = {
    "one_hop_aggregate": {
        "question": (
            "Account {aid} is under review for acting as a payment mule. How many distinct "
            "counterparty accounts has it sent funds to?"),
        "typology": "Mule account — outbound counterparty breadth",
        "source": ("FFIEC BSA/AML Appendix F — 'many-to-one' and 'one-to-many' funds flow; "
                   "counterparty count is the first screen in a customer risk review"),
        "why_it_matters": (
            "Breadth of outbound counterparties separates a normal retail account from a "
            "distribution point. It is the cheapest question an analyst asks and the one they "
            "ask first, so its cost sets the floor for everything downstream."),
    },
    "two_hop_aggregate": {
        "question": (
            "We are considering freezing account {aid}. Within two transfer hops downstream, "
            "how many other accounts would be touched by funds that passed through it? "
            "Exclude account {aid} itself."),
        "typology": "Exposure / containment scoping",
        "source": ("FATF layering typology — funds are moved through intermediaries before "
                   "integration; a freeze decision needs the downstream footprint"),
        "why_it_matters": (
            "This is the question that decides an operational action, and it cannot stop early "
            "— an approximate footprint is not a footprint. On a hub account it is also the "
            "question that does not return, which is precisely why it is in the set."),
    },
    "one_hop_list": {
        "question": (
            "For the suspicious activity report on account {aid}, list every counterparty "
            "account it sent funds to."),
        "typology": "SAR attachment — counterparty enumeration",
        "source": ("FinCEN SAR narrative guidance — identify the parties and the flow of funds; "
                   "an enumerated list is attached, not a count"),
        "why_it_matters": (
            "A filing needs the parties named, so a truncated list is a defective filing rather "
            "than a rounded answer. This is where the row cap stops being a performance setting "
            "and becomes a correctness one."),
    },
}

# Bands to draw anchors from, smallest first so a reader can see where behaviour changes.
BANDS = ("small", "medium", "large", "huge")
LIST_CAP = 10

# The row cap the guardrail applies to a result. Recorded on each case so a question whose
# true answer exceeds it is *visible as such* rather than silently scored against a
# truncated answer — the largest case in this set has a true answer of 908,649 accounts.
GUARDRAIL_ROW_CAP = 50


def _difficulty(reasoning_type: str, band: str, l1: int, l2: int,
                answer_size: int) -> Dict[str, Any]:
    """Difficulty as a vector, because the measurements showed the axes are independent.

    A single easy/medium/hard label would hide the distinctions that produced every result
    in this experiment. The clearest proof that the axes do not co-vary: the highest-cost
    anchor in the set (L2 51,447,907) answers a one-hop question in 154,077 db hits and
    returns, while a *cheaper* anchor at L2 3,773 asked for an aggregate cannot stop early.
    Cost and terminability are orthogonal, and collapsing them loses the one that decides
    whether a query returns at all.

    ``cost_band``
        Which L2 quantile the anchor sits in. Predicts engine work at ~2x for L2 above
        roughly 3,800.
    ``terminable``
        Whether the answer permits stopping early. On a hub this is the single strongest
        determinant of whether the query returns.
    ``answer_fits_row_cap``
        Whether the true answer fits under the guardrail's row cap. When it does not, a
        bounded answer is not an approximation — it is a different question's answer, and
        the honest behaviour is to disclose the truncation.
    ``anchor_kind``
        ``exact_key`` resolves through an index seek; a text anchor's candidate set grows
        with the graph. Measured: a text-matched anchor answered 25 correctly at SF1 and
        **0** at SF1000, because the scan both explodes in cost and stops resolving.
    ``distractor_density``
        Whether plausible-but-wrong candidates exist. Anchored questions have none by
        construction; the unanchored ring question competes with 471,151 incidental cycles
        on the realistic graph against 47 on the uniform one, which is why precision is
        free at small scale and not at large.
    """
    terminable = reasoning_type.endswith("_list")
    return {
        "cost_band": band,
        "anchor_l1": l1,
        "anchor_l2": l2,
        "terminable": terminable,
        "answer_size": answer_size,
        "answer_fits_row_cap": answer_size <= GUARDRAIL_ROW_CAP,
        "anchor_kind": "exact_key",
        "distractor_density": "none_anchored",
        "direction_ambiguous": True,  # TRANSFER has Account on both ends
        "edge_types_traversed": 1,
    }

# Wordings deliberately absent from the ontology's declared source/target phrases. The
# declared list is a fast path, and a fast path that is also the only path is just
# overfitting: substring matching scored 0 of 6 on these when they were first tried. Their
# purpose is to check the mechanism generalises — the ontology names the roles and the
# model maps arbitrary phrasing onto them — rather than that a phrase list happened to
# cover the questions its author wrote.
PARAPHRASES = (
    ("paid", "How many accounts did the account with account number {aid} pay?", "fan_out"),
    ("beneficiaries", "How many beneficiaries did the account with account number {aid} "
                      "send funds to?", "fan_out"),
    ("pushed", "How many distinct accounts did the account with account number {aid} push "
               "funds toward?", "fan_out"),
)


def _gold_for(con, anchor_id: int, transfer: str) -> Dict[str, Any]:
    fan_out = con.execute(
        f"SELECT count(DISTINCT dst) FROM '{transfer}' WHERE src = ?", [anchor_id]
    ).fetchone()[0]
    # "Reachable *within* 2 hops" is the union of one and two hops, and excludes the
    # anchor itself. The first version of this gold computed *exactly* two hops and
    # included the anchor, so all four two-hop cases were scored wrong while the agent
    # was answering the question as asked — 3 of 4 matched the correct reading exactly and
    # the fourth differed by one, the anchor. Recorded because a wrong gold is
    # indistinguishable from a wrong answer in the score, and reads as a model failure.
    two_hop = con.execute(
        f"""
        WITH h1 AS (SELECT DISTINCT dst AS n FROM '{transfer}' WHERE src = ?),
             h2 AS (SELECT DISTINCT b.dst AS n
                    FROM '{transfer}' a JOIN '{transfer}' b ON b.src = a.dst
                    WHERE a.src = ?)
        SELECT count(*) FROM (SELECT n FROM h1 UNION SELECT n FROM h2) u
        WHERE u.n <> ?
        """,
        [anchor_id, anchor_id, anchor_id],
    ).fetchone()[0]
    sample = [r[0] for r in con.execute(
        f"SELECT DISTINCT dst FROM '{transfer}' WHERE src = ? ORDER BY dst LIMIT {LIST_CAP}",
        [anchor_id]).fetchall()]
    return {"fan_out": int(fan_out), "two_hop": int(two_hop), "sample": sample}


def main() -> None:
    import duckdb

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--params", type=Path, default=None,
                        help="defaults to <src>/curated_parameters.json")
    parser.add_argument("--bands", default=",".join(BANDS))
    parser.add_argument("--paraphrase", action="store_true",
                        help="also emit the same question in wordings absent from the "
                             "ontology's declared phrase list, to test that role "
                             "resolution generalises instead of matching strings")
    parser.add_argument("--paraphrase-band", default="medium")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    params = args.params or (args.src / "curated_parameters.json")
    curated = json.loads(params.read_text())
    wanted = [b.strip() for b in args.bands.split(",") if b.strip()]
    by_band = {b["band"]: b for b in curated["bands"]}

    transfer = str(args.src / "edges" / "transfer.parquet")
    con = duckdb.connect()

    cases: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for band in wanted:
        entry = by_band.get(band)
        if not entry or not entry["anchors"]:
            continue
        anchor = entry["anchors"][0]
        aid = anchor["account_id"]
        gold = _gold_for(con, aid, transfer)
        common = {
            "band": band,
            "anchor_account": aid,
            "anchor_l1": anchor["l1"],
            "anchor_l2": anchor["l2"],
        }
        cases.append({
            "id": f"{band}_fan_out_count",
            "category": "aggregation",
            "reasoning_type": "one_hop_aggregate",
            "question": TEMPLATES["one_hop_aggregate"]["question"].format(aid=aid),
            **{k: v for k, v in TEMPLATES["one_hop_aggregate"].items() if k != "question"},
            "gold": [str(gold["fan_out"])],
            "scenario": "hub-degree",
            "difficulty": _difficulty("one_hop_aggregate", band, anchor["l1"],
                                      anchor["l2"], gold["fan_out"]),
            **common,
        })
        cases.append({
            "id": f"{band}_twohop_count",
            "category": "aggregation",
            "reasoning_type": "two_hop_aggregate",
            "question": TEMPLATES["two_hop_aggregate"]["question"].format(aid=aid),
            **{k: v for k, v in TEMPLATES["two_hop_aggregate"].items() if k != "question"},
            "gold": [str(gold["two_hop"])],
            "scenario": "hub-degree",
            "difficulty": _difficulty("two_hop_aggregate", band, anchor["l1"],
                                      anchor["l2"], gold["two_hop"]),
            **common,
        })
        if gold["fan_out"] > LIST_CAP:
            skipped.append({"band": band, "fan_out": gold["fan_out"],
                            "reason": "fan-out exceeds the cap, so any correct answer is "
                                      "one of many valid subsets and ordering would decide "
                                      "the score"})
            continue
        cases.append({
            "id": f"{band}_fan_out_list",
            "category": "retrieval",
            "reasoning_type": "one_hop_list",
            "question": TEMPLATES["one_hop_list"]["question"].format(aid=aid),
            **{k: v for k, v in TEMPLATES["one_hop_list"].items() if k != "question"},
            "gold": [str(x) for x in gold["sample"]],
            "scenario": "hub-degree",
            "difficulty": _difficulty("one_hop_list", band, anchor["l1"],
                                      anchor["l2"], len(gold["sample"])),
            **common,
        })

    if args.paraphrase:
        # One band is enough: the question is whether phrasing survives, not whether
        # degree does — degree is already covered by the primary set.
        entry = by_band.get(args.paraphrase_band)
        if entry and entry["anchors"]:
            anchor = entry["anchors"][0]
            aid = anchor["account_id"]
            g = _gold_for(con, aid, transfer)
            for slug, template, key in PARAPHRASES:
                cases.append({
                    "id": f"paraphrase_{slug}",
                    "category": "aggregation",
                    "reasoning_type": "one_hop_aggregate",
                    "question": template.format(aid=aid),
                    "gold": [str(g[key])],
                    "scenario": "hub-degree-paraphrase",
                    "difficulty": _difficulty("one_hop_aggregate", args.paraphrase_band,
                                              anchor["l1"], anchor["l2"], g[key]),
                    "band": args.paraphrase_band,
                    "anchor_account": aid,
                    "anchor_l1": anchor["l1"],
                    "anchor_l2": anchor["l2"],
                })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    # Same envelope as cases.json — the harnesses read `["cases"]`, and a bare list
    # fails there with a TypeError rather than a useful message.
    args.out.write_text(json.dumps({
        "schema_version": "seocho.finbench.cases.hub.v1",
        # Machine-readable, because anchors are snapshot-specific integers. Pairing these
        # cases with a cost model built from a *different* snapshot yields confident,
        # meaningless predictions — the same class of error as a wrong gold: the numbers
        # look fine and describe nothing.
        "curated_from": str(args.src),
        "note": (
            "Anchored on curated anchors (by intermediate result size), not on planted "
            "typology ids. The planted set is unusable for this question because its "
            "anchors sit at reserved ids outside the random attachment range, so their "
            "degrees are 2-26 against a graph maximum of 158,315 — the case set cannot "
            "see the degree distribution at all. Gold is computed in DuckDB over the "
            "same Parquet the graph was loaded from."
        ),
        "cases": cases,
        # Recorded rather than dropped silently: a case set that quietly omits bands reads
        # as full coverage when it is not.
        "skipped_list_cases": skipped,
    }, indent=2) + "\n")

    print(f"wrote {len(cases)} cases to {args.out}")
    for sk in skipped:
        print(f"  skipped {sk['band']}_fan_out_list (fan_out={sk['fan_out']:,} > cap {LIST_CAP})")
    print(f"{'band':8s} {'anchor':>10s} {'L1':>8s} {'L2':>12s} {'fan_out':>9s} {'two_hop':>12s}")
    for band in wanted:
        rows = [c for c in cases if c["band"] == band]
        if not rows:
            continue
        fan = next(c for c in rows if c["id"].endswith("fan_out_count"))
        two = next(c for c in rows if c["id"].endswith("twohop_count"))
        print(f"{band:8s} {fan['anchor_account']:>10,} {fan['anchor_l1']:>8,} "
              f"{fan['anchor_l2']:>12,} {int(fan['gold'][0]):>9,} "
              f"{int(two['gold'][0]):>12,}")


if __name__ == "__main__":
    main()
