#!/usr/bin/env python3
"""Does ontology grounding matter *more* as scale and complexity grow?

The single-point ablation at SF1000 showed a large gap (full 67% / 75% sargable vs
labels-only 0% / 0%) but a single point cannot show a trend. The claim that needs
evidence is stronger than "the ontology helps": it is that **the value of the
ontology grows with scale and with schema complexity**.

So this runs a grid:

    arms   : full ontology  vs  labels-only (the bare-LLM position)
    scales : SF1 .. SF1000 (same planted gold at every scale factor)
    tiers  : core (Account + TRANSFER only)  vs  schema_rich (needs channel
             knowledge — the Channel label, USES_CHANNEL, or a channel/amount
             predicate)

and reports the *gap* (full − minimal) per cell. If the gap widens with SF, the
ontology is not a convenience at scale — it is load-bearing. If it widens faster
on schema_rich than on core, then schema cardinality, not just data volume, is
what makes grounding necessary.

Why the gap can widen even when accuracy is the metric: a labels-only agent writes
unlabeled patterns, which cannot use an index. At SF1 a full scan is cheap and
still returns the right rows, so the answer is often correct anyway. At SF1000 the
same query scans millions of rows, and either times out or resolves its anchor
incorrectly — so the identical prompt degrades purely because of scale.

Usage:
    python scripts/finbench/guardrail_value_grid.py \
        --scales 1,10,100,1000 --db-prefix finbenchsf \
        --ontology examples/finbench/finbench.ontology.yaml \
        --cases examples/finbench/cases.json \
        --uri bolt://localhost:7687 --user neo4j --password "$NEO4J_PASSWORD" \
        --model gpt-oss-120b --out outputs/finbench/guardrail_value_grid.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


breakdown = _load("finbench_breakdown", "mara_breakdown.py")
ablation = _load("finbench_ablation", "ablation_ontology.py")
instrumentation = _load("finbench_instrumentation", "instrumentation.py")


def _tier_stats(cases: list[dict], tier: str) -> dict:
    subset = [c for c in cases if c.get("tier") == tier] if tier else cases
    if not subset:
        return {"cases": 0}
    stats = instrumentation.aggregate(subset)
    return {"cases": len(subset), "accuracy": stats["s5_accuracy"],
            "sargable": stats["s4_sargable_rate"], "db_hits": stats["s4_db_hits_total"],
            "supported": stats["s3_supported_rate"], "slot_fill": stats["s2_slot_fill_rate"]}


def _fmt(v, pct=True) -> str:
    if v is None:
        return "n/a"
    return f"{v:.0%}" if pct else f"{v:,.0f}"


def _markdown(report: dict) -> str:
    lines = [
        "# Does the ontology matter more as scale and complexity grow?", "",
        f"model `{report['model']}` · arms differ only in the schema handed to the agent", "",
        "## Accuracy by arm and scale", "",
        "| SF | nodes | full ontology | labels only | gap (pp) |",
        "|---|---|---|---|---|",
    ]
    for cell in report["cells"]:
        f, m = cell["arms"]["full"], cell["arms"]["minimal"]
        gap = (f["accuracy"] - m["accuracy"]) * 100
        lines.append(f"| {cell['scale_factor']} | {cell.get('nodes', '?'):,} | "
                     f"{_fmt(f['accuracy'])} | {_fmt(m['accuracy'])} | **{gap:+.0f}** |")

    lines += ["", "## Plan quality (S4 sargable) by arm and scale", "",
              "The mechanism: grounding steers the model toward index-usable shapes.", "",
              "| SF | full ontology | labels only | gap (pp) |", "|---|---|---|---|"]
    for cell in report["cells"]:
        f, m = cell["arms"]["full"], cell["arms"]["minimal"]
        fs, ms = f["stages"].get("s4_sargable_rate"), m["stages"].get("s4_sargable_rate")
        gap = ((fs or 0) - (ms or 0)) * 100
        lines.append(f"| {cell['scale_factor']} | {_fmt(fs)} | {_fmt(ms)} | **{gap:+.0f}** |")

    lines += ["", "## Database work performed (total dbHits across the 9 questions)", "",
              "| SF | full ontology | labels only | ratio |", "|---|---|---|---|"]
    for cell in report["cells"]:
        f, m = cell["arms"]["full"], cell["arms"]["minimal"]
        fh = f["stages"].get("s4_db_hits_total") or 0
        mh = m["stages"].get("s4_db_hits_total") or 0
        ratio = f"{mh / fh:.1f}x" if fh else "n/a"
        lines.append(f"| {cell['scale_factor']} | {fh:,} | {mh:,} | {ratio} |")

    lines += ["", "## Complexity axis — accuracy gap by tier", "",
              "`core` = Account + TRANSFER only · `schema_rich` = needs channel knowledge", "",
              "| SF | core: full | core: minimal | core gap | rich: full | rich: minimal | rich gap |",
              "|---|---|---|---|---|---|---|"]
    for cell in report["cells"]:
        row = [str(cell["scale_factor"])]
        for tier in ("core", "schema_rich"):
            f = cell["tiers"][tier]["full"]
            m = cell["tiers"][tier]["minimal"]
            gap = ((f.get("accuracy") or 0) - (m.get("accuracy") or 0)) * 100
            row += [_fmt(f.get("accuracy")), _fmt(m.get("accuracy")), f"**{gap:+.0f}**"]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scales", default="1,10,100,1000")
    parser.add_argument("--db-prefix", default="finbenchsf")
    parser.add_argument("--ontology", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="")
    parser.add_argument("--model", default="gpt-oss-120b")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    from neo4j import GraphDatabase
    from seocho.ontology import Ontology

    full = Ontology.load(args.ontology)
    minimal = ablation.minimal_ontology(full)
    cases = json.loads(args.cases.read_text())["cases"]
    scales = [int(s) for s in args.scales.split(",") if s.strip()]

    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
    cells = []
    try:
        for sf in scales:
            database = f"{args.db_prefix}{sf}"
            with driver.session(database=database) as session:
                nodes = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            cell: dict = {"scale_factor": sf, "database": database, "nodes": nodes,
                          "arms": {}, "tiers": {"core": {}, "schema_rich": {}}}
            for arm, ontology in (("full", full), ("minimal", minimal)):
                print(f"[grid] SF{sf} arm={arm} ...", flush=True)
                result = breakdown._run_model(
                    args.model, ontology, args.uri, args.user, args.password,
                    database, cases, True)
                cell["arms"][arm] = result
                for tier in ("core", "schema_rich"):
                    cell["tiers"][tier][arm] = _tier_stats(result["cases"], tier)
            cells.append(cell)
    finally:
        driver.close()

    report = {"schema_version": "seocho.finbench.guardrail-value-grid.v1",
              "model": args.model, "cells": cells}
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
        args.out.with_suffix(".md").write_text(_markdown(report))
    print(_markdown(report))


if __name__ == "__main__":
    main()
