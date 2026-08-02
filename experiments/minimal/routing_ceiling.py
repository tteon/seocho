#!/usr/bin/env python3
"""What could routing buy, at best, given what the graphs actually contain?

Part 1 measured that most facts live in exactly one view: two of three models
name the same fact only 20% to 34% of the time depending on the schema. The
obvious reading is that choosing which view to ask therefore matters, and that
is the argument a routing method would rest on.

It has a hole in it, and this measures the hole before the method is proposed.
Asking every view always covers at least as much as asking one, so a router can
never beat the union on coverage. If routing is worth anything it is worth it on
cost, and the honest claim has that shape: this fraction of the union's coverage
for this fraction of its cost. Anything stronger is unsupportable and a reviewer
will say so first.

Four strategies over the same graphs and the same gold answers:

    fixed        one view, the same one for every case. The floor, and the
                 cheapest thing that could work
    best fixed   the single view that happens to do best overall — still one
                 view, but chosen with hindsight, so it is an upper bound on
                 any router that cannot see the case
    oracle       per case, the view that covers the most of the gold answer.
                 No router can beat this, so it is the ceiling routing aims at
    union        every view. The coverage ceiling, at the full cost

Coverage is the share of the gold answer's figures present in the consulted
graphs, matched within 1% with scale words applied. Cost is views consulted,
which is the quantity a router actually saves.

Reads the committed snapshots, so it runs from a fresh clone with no database
and no model call.
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


def covered(wanted: set[float], available: set[float]) -> int:
    return sum(1 for w in wanted if any(close(w, a) for a in available))


def load_view(path: Path) -> set[float]:
    """Every figure a workspace holds, from names and values alike."""
    found: set[float] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("kind") != "node":
            continue
        if set(record.get("labels") or []) & INFRA:
            continue
        props = record.get("props") or {}
        for key in ("value", "amount", "name"):
            if props.get(key):
                found |= numbers_in(props[key])
    return found


def load_cases() -> dict[str, dict[str, Any]]:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "finder_index", ROOT / "examples/mdm/11_index_providers.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {c["case_id"]: c for c in module.load_cases_full(seed=42)}


def evaluate(views: dict[str, dict[str, set[float]]],
             gold: dict[str, set[float]]) -> dict[str, Any]:
    """Coverage and cost for each strategy, over the cases that have a figure."""
    cases = [c for c in sorted(gold) if gold[c]]
    per_model: dict[str, list[float]] = defaultdict(list)
    oracle, union = [], []
    oracle_choice: dict[str, int] = defaultdict(int)

    for case in cases:
        wanted = gold[case]
        rates = {}
        everything: set[float] = set()
        for model in MODELS:
            available = views.get(case, {}).get(model, set())
            everything |= available
            rates[model] = covered(wanted, available) / len(wanted)
            per_model[model].append(rates[model])
        best = max(rates, key=lambda m: rates[m])
        oracle_choice[best] += 1
        oracle.append(rates[best])
        union.append(covered(wanted, everything) / len(wanted))

    def mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 4) if values else 0.0

    fixed = {m: mean(v) for m, v in per_model.items()}
    best_fixed_model = max(fixed, key=lambda m: fixed[m])

    strategies = {
        "fixed_worst": {"coverage": min(fixed.values()), "views": 1.0},
        "fixed_best": {"coverage": fixed[best_fixed_model], "views": 1.0,
                       "view": best_fixed_model},
        "oracle_routing": {"coverage": mean(oracle), "views": 1.0},
        "union_all_views": {"coverage": mean(union), "views": float(len(MODELS))},
    }
    ceiling = strategies["union_all_views"]["coverage"] or 1.0
    for cell in strategies.values():
        cell["share_of_union"] = round(cell["coverage"] / ceiling, 4)
        cell["coverage"] = round(cell["coverage"], 4)
    return {"cases_scored": len(cases), "per_view": fixed,
            "oracle_picks": dict(oracle_choice), "strategies": strategies}


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

    run = observe.Run(OUT_ROOT, "routing-ceiling", {"decisive": {
        "tag": args.tag, "arms": arms, "models": list(MODELS),
        "coverage": "gold figures present, 1% tolerance, scale words applied",
        "seed": 42}})

    with run.stage("gold") as out:
        cases = load_cases()
        out["cases_available"] = len(cases)

    results: dict[str, Any] = {}
    for arm in arms:
        with run.stage(f"read.{arm}", arm=arm) as out:
            views: dict[str, dict[str, set[float]]] = defaultdict(dict)
            for path in sorted(directory.glob(f"{arm}_*.jsonl")):
                parts = path.stem.split("_")
                model, case = parts[1], "_".join(parts[2:])
                views[case][model] = load_view(path)
            out["cases"] = len(views)
        with run.stage(f"score.{arm}", arm=arm) as out:
            gold = {case: numbers_in(cases.get(case, {}).get("expected_answer", ""))
                    for case in views}
            results[arm] = evaluate(views, gold)
            for name, cell in results[arm]["strategies"].items():
                out[name] = f"{cell['coverage']:.3f} ({cell['share_of_union']:.0%})"

    payload = {
        "contract": "log2026.routing_ceiling.v1",
        "question": ("Given what the graphs contain, what is the most any "
                     "router could gain, and at what cost?"),
        "method": ("four strategies over the committed snapshots: one fixed "
                   "view, the best fixed view chosen with hindsight, per-case "
                   "oracle selection, and the union of all views. Coverage is "
                   "the share of the gold answer's figures present, matched "
                   "within 1% with scale words applied. Cost is views "
                   "consulted."),
        "claim_boundary": ("An upper bound on routing, not a measurement of any "
                           "router. The oracle sees the gold answer, so no "
                           "implementable method reaches it. Coverage means the "
                           "figure is somewhere in the consulted graphs; it does "
                           "not mean a query would retrieve it or an answer "
                           "would use it."),
        "tag": args.tag, "models": list(MODELS),
        "by_condition": results,
    }
    (run.dir / "routing_ceiling.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print()
    for arm in arms:
        cell = results[arm]
        print(f"condition {arm}  ({cell['cases_scored']} cases with a figure "
              f"in the gold answer)")
        print(f"  {'strategy':18s} {'coverage':>9s} {'views':>6s} {'of union':>9s}")
        for name, s in cell["strategies"].items():
            print(f"  {name:18s} {s['coverage']:9.3f} {s['views']:6.1f} "
                  f"{s['share_of_union']:9.0%}")
        print(f"  oracle would pick: {cell['oracle_picks']}")
        print()

    run.finish({"by_condition": {a: results[a]["strategies"] for a in arms},
                "artifact": str((run.dir / "routing_ceiling.json").relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
