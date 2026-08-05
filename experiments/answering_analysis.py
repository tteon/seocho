#!/usr/bin/env python3
"""Freeze the answering experiment's analysis as an artifact.

The paired bootstraps, evidence-conditional decomposition, grounding rates
and refusal counts for a tag were first computed interactively; this script
recomputes them deterministically from the partials and writes them under a
contract (`log2026.answering_analysis.<tag>`), so the findings tree and the
narrative grounding checker cite an artifact rather than a conversation.

    python3 experiments/answering_analysis.py --tag an1
    python3 experiments/answering_analysis.py --tag an2 --graph-a-tag s3 --graph-c-tag s3
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/minimal"))

import observe  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "answering", ROOT / "experiments/answering.py")
assert spec and spec.loader
answering = importlib.util.module_from_spec(spec)
spec.loader.exec_module(answering)

MODELS = ("gptoss", "minimax27", "deepseek")
CONDITIONS = ("closed_book", "passages", "graph_a", "graph_c",
              "graph_c_anchors")
DRAWS = 5000
YEAR = (1900, 2100)   # tokens in this band are years, not figures


def load(tag: str, cond: str, model: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in (ROOT / "outputs/evaluation/answering" / tag / cond
                 / model).glob("*.json"):
        record = json.loads(path.read_text())
        out[record["case"]] = record
    return out


def bootstrap(values: list[float]) -> dict[str, Any]:
    rng = random.Random(42)
    count = len(values)
    means = sorted(
        statistics.mean([values[rng.randrange(count)] for _ in range(count)])
        for _ in range(DRAWS))
    return {"mean": round(statistics.mean(values), 4),
            "ci95": [round(means[125], 4), round(means[4874], 4)],
            "n": count,
            "separated": means[125] > 0 or means[4874] < 0}


def paired(a: dict, b: dict) -> dict[str, Any]:
    values = [a[c]["number_overlap"] - b[c]["number_overlap"] for c in a
              if c in b and a[c]["number_overlap"] is not None
              and b[c]["number_overlap"] is not None]
    return bootstrap(values)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="an1")
    parser.add_argument("--graph-a-tag", default="s1")
    parser.add_argument("--graph-c-tag", default="s2")
    args = parser.parse_args()

    cases = answering.load_cases()
    a_views = answering.sweep_case_ids(args.graph_a_tag, "A")
    c_views = answering.sweep_case_ids(args.graph_c_tag, "C")
    anchors = answering.load_anchor_index(args.graph_c_tag)

    evidence_cache: dict[tuple[str, str], set[float]] = {}

    def evidence_figs(cond: str, cid: str) -> set[float]:
        key = (cond, cid)
        if key not in evidence_cache:
            if cond == "passages":
                text = "\n".join(cases[cid]["references"])
            elif cond == "graph_a":
                text = answering.serialize_graph(a_views[cid], None, cid)
            elif cond.startswith("graph_c"):
                text = answering.serialize_graph(
                    c_views[cid],
                    anchors if cond.endswith("anchors") else None, cid)
            else:
                text = ""
            evidence_cache[key] = set(answering.scoring_figures(text))
        return evidence_cache[key]

    def ec_verdict(cond: str, record: dict, cid: str) -> str | None:
        gold = {g for g in answering.gold_figures(
            cases[cid]["expected_answer"])
            if not (YEAR[0] <= abs(g) <= YEAR[1])}
        if not gold:
            return None
        refused = "cannot determine" in record["answer"].lower()
        hit = (record["number_overlap"] or 0) > 0
        if cond == "closed_book":
            has = False
        else:
            ev = evidence_figs(cond, cid)
            has = any(any(answering.provenance.close(g, e) for e in ev)
                      for g in gold)
        if has:
            return ("grounded_correct" if hit
                    else "over_refusal" if refused else "utilization_failure")
        return ("honest_abstention" if refused
                else "contaminated" if hit else "hallucination")

    run = observe.Run(ROOT / "outputs/minimal", "answering-analysis", {
        "contract": f"log2026.answering_analysis.{args.tag}",
        "decisive": {"tag": args.tag, "draws": DRAWS, "seed": 42,
                     "graph_a_tag": args.graph_a_tag,
                     "graph_c_tag": args.graph_c_tag,
                     "agreement": "close() at 0.1% after scale words",
                     "year_band_excluded": list(YEAR)},
    })

    books = {(c, m): load(args.tag, c, m)
             for c in CONDITIONS for m in MODELS}
    present_models = [m for m in MODELS
                      if any(books[(c, m)] for c in CONDITIONS)]

    result: dict[str, Any] = {"models": present_models}
    with run.stage("conditions") as out:
        table: dict[str, Any] = {}
        for model in present_models:
            for cond in CONDITIONS:
                rows = books[(cond, model)]
                if not rows:
                    continue
                scored = [r["number_overlap"] for r in rows.values()
                          if r["number_overlap"] is not None]
                table[f"{cond}/{model}"] = {
                    "attempted": len(rows),
                    "failed": sum(1 for r in rows.values()
                                  if r["status"] != "ok"),
                    "number_overlap": (round(statistics.mean(scored), 4)
                                       if scored else None),
                    "refusals": sum(1 for r in rows.values() if
                                    "cannot determine" in r["answer"].lower()),
                }
        out["table"] = table
        result["conditions"] = table

    with run.stage("paired") as out:
        pairs: dict[str, Any] = {}
        for model in present_models:
            for left, right in (("passages", "closed_book"),
                                ("passages", "graph_a"),
                                ("passages", "graph_c"),
                                ("graph_a", "closed_book"),
                                ("graph_c_anchors", "graph_c")):
                a, b = books[(left, model)], books[(right, model)]
                if a and b:
                    pairs[f"{left}-{right}/{model}"] = paired(a, b)
        out["pairs"] = pairs
        result["paired"] = pairs

    with run.stage("evidence_conditional") as out:
        matrix: dict[str, Any] = {}
        for model in present_models:
            for cond in CONDITIONS:
                rows = books[(cond, model)]
                if not rows:
                    continue
                counts = Counter(v for cid, r in rows.items()
                                 if (v := ec_verdict(cond, r, cid)))
                matrix[f"{cond}/{model}"] = dict(counts)
        out["matrix"] = matrix
        result["evidence_conditional"] = matrix

    # The registry (experiments/registry.py) indexes artifacts carrying
    # contract + question + claim_boundary; without this file the analysis
    # exists only as a run directory and the findings tree cannot cite it.
    artifact = {
        "contract": f"log2026.answering_analysis.{args.tag}",
        "question": ("Under five evidence conditions, which differences in "
                     "answer quality are real, and which answers actually "
                     "used the evidence they were served?"),
        "claim_boundary": (
            "Paired bootstrap over cases (5,000 draws, seed 42), per model — "
            "never across models. 'Evidence contains the answer' is a "
            "numeric-token approximation; grounded means overlap > 0, so "
            "partial answers count. Gold answers without figures are "
            "excluded from the primary metric, not zeroed."),
        **result,
    }
    (run.dir / "answering_analysis.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=1))
    run.finish(result)
    print(json.dumps(result["conditions"], indent=1)[:600])


if __name__ == "__main__":
    main()
