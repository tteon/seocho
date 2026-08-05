#!/usr/bin/env python3
"""Is refusing to answer, when views disagree, worth anything?

Part 1 found that independently built graphs barely agree on names but hold much
the same figures, and the routing measurement found that choosing between views
buys essentially nothing — the oracle beats a fixed choice by two points, and
on real cross-view cases the answer advantage is not distinguishable from zero.

If selection is not the product, verification might be. A federation of views
can do one thing a single source cannot: notice that two of them say different
things about the same fact, and decline to serve it. That is only worth
something if the refusal is *informative* — if the facts it withholds were the
ones that would have been wrong.

So this scores the refusal signal the way a classifier is scored, against the
dataset's own answer:

The population has to be chosen carefully, and a first attempt at this got it
wrong in a way worth recording. Scoring every extracted fact against the gold
answer made 95% of them "wrong" — but a graph holds every figure in the
document and the answer contains a handful, so almost all of that 95% was not
wrong, merely irrelevant. Refusal precision came out at 0.95 against a base
error rate of 0.95: exactly chance, and the two numbers were measuring the same
thing.

So the population is restricted to **answer-relevant facts**: those where at
least one view's figure matches the gold answer. On those, serving the wrong
value is a real error rather than an irrelevance, and two policies can be
compared on the same footing:

    serve always     take the first view's figure, as a single source must
    serve if agreed  serve only when the views that hold the fact agree

Precision is the share of served figures that match gold; recall is the share
of answer-relevant facts served correctly. Verification can only trade the
second for the first, and the question is whether the trade is favourable.

Facts only one view holds are counted separately throughout. No cross-view
mechanism can rule on them, and their share is the ceiling Part 1 measured.

Reads the committed snapshots. No database and no model call, so it runs from a
fresh clone.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for path in (str(HERE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

SNAPSHOTS = ROOT / "snapshots"
OUT_ROOT = ROOT / "outputs/minimal"
MODELS = ("deepseek", "gptoss", "minimax27")
INFRA = {"Document", "Chunk", "Version", "DocumentVersion", "Section",
         "__Memory__", "Memory"}

_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")
_SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def parse_amount(text: str) -> float | None:
    """One figure with its scale word applied, so 59.4 and 59.4 million differ."""
    raw = str(text or "")
    found = _NUMBER.search(raw)
    if not found:
        return None
    try:
        value = float(found.group(0).replace(",", ""))
    except ValueError:
        return None
    lowered = raw.lower()
    for word, factor in _SCALE.items():
        if re.search(rf"\b{word}s?\b", lowered):
            value *= factor
            break
    if "(" in raw and ")" in raw:
        value = -abs(value)
    return value


def numbers_in(text: str) -> set[float]:
    raw = str(text or "")
    lowered = raw.lower()
    factor = 1.0
    for word, value in _SCALE.items():
        if re.search(rf"\b{word}s?\b", lowered):
            factor = value
            break
    found = set()
    for match in _NUMBER.finditer(raw):
        try:
            value = float(match.group(0).replace(",", ""))
        except ValueError:
            continue
        if value == 0:
            continue
        found.add(value)
        if factor != 1.0:
            found.add(value * factor)
    return found


def close(a: float, b: float, tolerance: float = 0.01) -> bool:
    if a == b:
        return True
    scale = max(abs(a), abs(b))
    return scale > 0 and abs(a - b) / scale <= tolerance


def load_view(path: Path) -> dict[str, list[float]]:
    """Fact name -> the figures this view attached to it."""
    facts: dict[str, list[float]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("kind") != "node":
            continue
        if set(record.get("labels") or []) & INFRA:
            continue
        props = record.get("props") or {}
        name = normalize(props.get("name", ""))
        if not name:
            continue
        value = parse_amount(props.get("value") or props.get("amount") or "")
        if value is not None:
            facts[name].append(value)
    return facts


def load_cases() -> dict[str, dict[str, Any]]:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "finder_index", ROOT / "examples/mdm/11_index_providers.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {c["case_id"]: c for c in module.load_cases_full(seed=42)}


def adjudicate(per_model: dict[str, dict[str, list[float]]],
               gold: set[float]) -> list[dict[str, Any]]:
    """One row per fact name that at least two views hold with a figure.

    A fact only one view holds cannot be verified at all, and is reported
    separately rather than folded in — its correctness is unknowable by this
    mechanism, which is the ceiling Part 1 measured.
    """
    by_name: dict[str, dict[str, list[float]]] = defaultdict(dict)
    for model, facts in per_model.items():
        for name, values in facts.items():
            by_name[name][model] = values

    rows = []
    for name, holders in by_name.items():
        candidates = {m: v[0] for m, v in holders.items() if v}
        if len(candidates) < 2:
            rows.append({"name": name, "views": len(candidates),
                         "verdict": "unverifiable"})
            continue
        values = list(candidates.values())
        agree = all(close(a, b) for a, b in combinations(values, 2))
        matches_gold = any(any(close(v, g) for g in gold) for v in values)
        first = values[0]
        rows.append({
            "name": name, "views": len(candidates),
            "answer_relevant": matches_gold,
            "agreed": agree,
            "first_view_correct": any(close(first, g) for g in gold),
            "agreed_correct": (agree and any(close(first, g) for g in gold)),
            "candidates": values,
        })
    return rows


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Two serving policies over the same facts, scored where scoring means
    something: the facts for which a right answer was available at all."""
    single = [r for r in rows if r.get("verdict") == "unverifiable"]
    verifiable = [r for r in rows if r.get("verdict") != "unverifiable"]
    relevant = [r for r in verifiable if r["answer_relevant"]]

    always_served = len(relevant)
    always_correct = sum(1 for r in relevant if r["first_view_correct"])

    agreed = [r for r in relevant if r["agreed"]]
    agreed_correct = sum(1 for r in agreed if r["agreed_correct"])

    return {
        "unverifiable_single_view": len(single),
        "verifiable": len(verifiable),
        "answer_relevant": len(relevant),
        "serve_always": {
            "served": always_served, "correct": always_correct,
            "precision": (round(always_correct / always_served, 4)
                          if always_served else 0.0),
            "recall": (round(always_correct / len(relevant), 4)
                       if relevant else 0.0)},
        "serve_if_agreed": {
            "served": len(agreed), "correct": agreed_correct,
            "precision": (round(agreed_correct / len(agreed), 4)
                          if agreed else 0.0),
            "recall": (round(agreed_correct / len(relevant), 4)
                       if relevant else 0.0),
            "withheld": len(relevant) - len(agreed)},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="v2")
    ap.add_argument("--arms", default="A,C,D,E")
    args = ap.parse_args()

    import observe

    directory = SNAPSHOTS / (args.tag or "v1")
    if not (directory / "manifest.json").is_file():
        raise SystemExit(f"no snapshots under {directory}")
    arms = [a.strip().upper() for a in args.arms.split(",") if a.strip()]

    run = observe.Run(OUT_ROOT, "verification-value", {"decisive": {
        "tag": args.tag, "arms": arms, "models": list(MODELS),
        "agreement": "values within 1% after applying scale words",
        "seed": 42}})

    with run.stage("gold") as out:
        cases = load_cases()
        out["cases_available"] = len(cases)

    results: dict[str, Any] = {}
    for arm in arms:
        with run.stage(f"read.{arm}", arm=arm) as out:
            views: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(dict)
            for path in sorted(directory.glob(f"{arm}_*.jsonl")):
                parts = path.stem.split("_")
                model, case = parts[1], "_".join(parts[2:])
                views[case][model] = load_view(path)
            out["cases"] = len(views)

        with run.stage(f"adjudicate.{arm}", arm=arm) as out:
            rows, per_case = [], {}
            for case, per_model in views.items():
                gold = numbers_in(cases.get(case, {}).get("expected_answer", ""))
                if not gold:
                    continue
                case_rows = adjudicate(per_model, gold)
                per_case[case] = summarise(case_rows)
                rows += case_rows
            summary = summarise(rows)
            results[arm] = {**summary, "cases_scored": len(per_case)}
            out.update(summary)

    payload = {
        "contract": f"log2026.verification_value.{args.tag or 'v1'}",
        "question": ("When independently built views disagree about a figure, "
                     "does refusing to serve it avoid an error?"),
        "method": ("facts matched across views by normalized name; figures "
                   "compared within 1% with scale words applied. A fact two or "
                   "more views hold is served when they agree and withheld when "
                   "they do not, and each decision is then checked against the "
                   "dataset's answer. Facts only one view holds are reported "
                   "separately as unverifiable, since no cross-view mechanism "
                   "can rule on them."),
        "claim_boundary": ("Correctness is whether a figure appears in the gold "
                           "answer, not whether it answers the question; a view "
                           "holding the right number against the wrong entity "
                           "counts as correct. Scored per fact rather than per "
                           "answer, so this bounds what a serving policy could "
                           "do rather than measuring one. Restricted to facts "
                           "where a right answer existed, because scoring every "
                           "extracted figure against the answer makes 95% of "
                           "them 'wrong' when they are merely irrelevant."),
        "supersedes": ("the first version of this measurement, whose refusal "
                       "precision of 0.95 sat exactly on a base error rate of "
                       "0.95 because both were counting irrelevance as error"),
        "tag": args.tag, "models": list(MODELS),
        "by_condition": results,
    }
    (run.dir / "verification_value.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print()
    print(f"{'cond':5s} {'relevant':>9s}  {'always: prec':>13s} {'recall':>7s}  "
          f"{'if agreed: prec':>16s} {'recall':>7s} {'withheld':>9s}  {'1-view':>7s}")
    for arm in arms:
        c = results[arm]
        a, g = c["serve_always"], c["serve_if_agreed"]
        print(f"{arm:5s} {c['answer_relevant']:9d}  {a['precision']:13.3f} "
              f"{a['recall']:7.3f}  {g['precision']:16.3f} {g['recall']:7.3f} "
              f"{g['withheld']:9d}  {c['unverifiable_single_view']:7d}")
    print("\npopulation  = facts where at least one view held a figure from the answer")
    print("always      = serve the first view's figure, as a single source must")
    print("if agreed   = serve only when the views holding the fact agree")
    print("1-view      = facts only one view holds; no verifier can rule on them")

    run.finish({"by_condition": results,
                "artifact": str((run.dir / "verification_value.json").relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
