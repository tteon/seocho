#!/usr/bin/env python3
"""Match facts by where they came from, not by what the model called them.

Every negative result in this study is the same failure wearing different
clothes. Identifiers are model-invented and match the entity's own name between
6% and 43% of the time. Giving the extractor an ontology makes names fragment
further, because more classes mean more ways to slice one sentence. Views hold
much the same figures under different names, so one view already has 95% of what
three have. And cross-view verification has three to six facts to rule on out of
hundreds.

All four are failures of the same decision: **matching on the name**, which is
the least reliable thing an extractor produces. Two things do not fragment. The
figure, which is why one view has 95% of the numbers. And the source — two
models reading the same sentence read the same sentence, whatever they then call
what they found.

So this keys facts by their anchor in the source text instead, and asks what
changes.

Finding the anchor
------------------
A fact carries a value string the model wrote, not a pointer into the document.
The anchor has to be recovered, and the recovery has to survive the very error
it is meant to catch: a model that read "$5.2 billion" and wrote 5200 produced a
number that does not appear in the text at all.

So for each fact the search is over surface forms — the value as parsed, and the
same mantissa at each scale — against every numeric token in the case's
reference passages. A fact anchors where one of its forms matches a token. Facts
whose forms match in more than one place are **dropped, not guessed**: an
ambiguous anchor would manufacture agreements, which is the opposite of the
point.

What this can show that name-keying cannot
------------------------------------------
Two views that read one sentence and wrote `revenue_2023 = $5.2B` and
`total_revenue = 5200` are, under name matching, two unrelated facts. Neither is
compared to the other and the scale error passes silently. Anchored to the same
token they become one fact with two values, and the disagreement is visible.
Scale errors were 67% of all disagreements the earlier census could see, so the
ones name-keying cannot see are likely the same kind.

Reads the committed snapshots and the dataset. No database, no model call.
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
SCALES = (1.0, 1e3, 1e6, 1e9, 1e12)


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def parse_amount(text: str) -> float | None:
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


def tokens_of(text: str) -> list[tuple[int, float]]:
    """Every numeric token in a passage, with the offset it sits at.

    The scale word that follows a token is applied, so the token for
    "$5.2 billion" carries 5.2e9 and can be matched by a model that wrote
    either form.
    """
    found = []
    for match in _NUMBER.finditer(text):
        try:
            value = float(match.group(0).replace(",", ""))
        except ValueError:
            continue
        if value == 0:
            continue
        trailing = text[match.end():match.end() + 24].lower()
        scaled = value
        for word, factor in _SCALE.items():
            if re.match(rf"\s*{word}s?\b", trailing):
                scaled = value * factor
                break
        found.append((match.start(), value))
        if scaled != value:
            found.append((match.start(), scaled))
    return found


def close(a: float, b: float, tolerance: float = 0.001) -> bool:
    if a == b:
        return True
    scale = max(abs(a), abs(b))
    return scale > 0 and abs(a - b) / scale <= tolerance


def anchor_of(value: float, passages: list[list[tuple[int, float]]]
              ) -> tuple[int, int] | None:
    """Where in the source this figure came from, or None if it is not clear.

    Exact matches are preferred; a scaled match is only used when no exact one
    exists, so a correctly-extracted figure never gets pulled to a
    wrongly-scaled neighbour. Either way, more than one candidate means the
    anchor is dropped rather than picked.
    """
    for forms in ((value,), tuple(value * s for s in SCALES[1:])
                  + tuple(value / s for s in SCALES[1:])):
        hits = {(index, offset)
                for index, tokens in enumerate(passages)
                for offset, token in tokens
                for form in forms if close(form, token)}
        if len(hits) == 1:
            return hits.pop()
        if hits:
            return None                  # ambiguous: refuse rather than guess
    return None


def load_view(path: Path) -> list[dict[str, Any]]:
    facts = []
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
        raw = props.get("value") or props.get("amount") or ""
        value = parse_amount(raw)
        if name and value is not None:
            facts.append({"name": name, "raw": str(raw), "value": value})
    return facts


def load_cases() -> dict[str, dict[str, Any]]:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "finder_index", ROOT / "examples/mdm/11_index_providers.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {c["case_id"]: c for c in module.load_cases_full(seed=42)}


def compare_keys(per_model: dict[str, list[dict[str, Any]]],
                 passages: list[list[tuple[int, float]]]) -> dict[str, Any]:
    """Pairs found by each key, and the disagreements each key can see."""
    by_name: dict[str, dict[str, float]] = defaultdict(dict)
    by_anchor: dict[tuple[int, int], dict[str, float]] = defaultdict(dict)
    anchored = unanchored = 0
    name_of_anchor: dict[tuple[int, int], dict[str, str]] = defaultdict(dict)

    for model, facts in per_model.items():
        for fact in facts:
            by_name[fact["name"]].setdefault(model, fact["value"])
            anchor = anchor_of(fact["value"], passages)
            if anchor is None:
                unanchored += 1
                continue
            anchored += 1
            by_anchor[anchor].setdefault(model, fact["value"])
            name_of_anchor[anchor].setdefault(model, fact["name"])

    def survey(groups: dict[Any, dict[str, float]]) -> dict[str, int]:
        multi = {k: v for k, v in groups.items() if len(v) >= 2}
        disagree = {k: v for k, v in multi.items()
                    if not all(close(a, b, 0.01)
                               for a, b in combinations(v.values(), 2))}
        return {"keys": len(groups), "comparable": len(multi),
                "disagreeing": len(disagree)}

    name_survey = survey(by_name)
    anchor_survey = survey(by_anchor)

    # The disagreements only the anchor can see: same source token, two values,
    # and the two views gave the fact different names so no name key existed.
    hidden = []
    for anchor, values in by_anchor.items():
        if len(values) < 2:
            continue
        if all(close(a, b, 0.01) for a, b in combinations(values.values(), 2)):
            continue
        names = set(name_of_anchor[anchor].values())
        if len(names) > 1:
            hidden.append({"anchor": list(anchor),
                           "names": sorted(names),
                           "values": sorted(values.values())})
    return {"anchored": anchored, "unanchored": unanchored,
            "by_name": name_survey, "by_anchor": anchor_survey,
            "hidden_disagreements": hidden}


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

    run = observe.Run(OUT_ROOT, "provenance-keying", {"decisive": {
        "tag": args.tag, "arms": arms, "models": list(MODELS),
        "anchor": ("unique numeric token in the case's reference passages, "
                   "exact match preferred over scaled; ambiguous anchors "
                   "dropped"),
        "agreement_tolerance": 0.01, "seed": 42}})

    with run.stage("corpus") as out:
        cases = load_cases()
        out["cases_available"] = len(cases)

    results: dict[str, Any] = {}
    for arm in arms:
        with run.stage(f"key.{arm}", arm=arm) as out:
            totals = {"anchored": 0, "unanchored": 0}
            name_total = {"keys": 0, "comparable": 0, "disagreeing": 0}
            anchor_total = {"keys": 0, "comparable": 0, "disagreeing": 0}
            hidden: list[dict[str, Any]] = []
            scored = 0

            per_case: dict[str, dict[str, list[dict]]] = defaultdict(dict)
            for path in sorted(directory.glob(f"{arm}_*.jsonl")):
                parts = path.stem.split("_")
                model, case = parts[1], "_".join(parts[2:])
                per_case[case][model] = load_view(path)

            for case, per_model in per_case.items():
                references = cases.get(case, {}).get("references") or []
                if not references:
                    continue
                passages = [tokens_of(text) for text in references]
                cell = compare_keys(per_model, passages)
                scored += 1
                totals["anchored"] += cell["anchored"]
                totals["unanchored"] += cell["unanchored"]
                for key in name_total:
                    name_total[key] += cell["by_name"][key]
                    anchor_total[key] += cell["by_anchor"][key]
                hidden += cell["hidden_disagreements"]

            facts = totals["anchored"] + totals["unanchored"]
            results[arm] = {
                "cases": scored, "facts_with_a_figure": facts,
                "anchored": totals["anchored"],
                "anchor_rate": round(totals["anchored"] / facts, 4) if facts else 0.0,
                "by_name": {**name_total,
                            "comparable_rate": round(
                                name_total["comparable"] / name_total["keys"], 4)
                            if name_total["keys"] else 0.0},
                "by_anchor": {**anchor_total,
                              "comparable_rate": round(
                                  anchor_total["comparable"] / anchor_total["keys"], 4)
                              if anchor_total["keys"] else 0.0},
                "disagreements_only_the_anchor_sees": len(hidden),
                "examples": hidden[:12],
            }
            out.update({k: v for k, v in results[arm].items() if k != "examples"})

    payload = {
        "contract": f"log2026.provenance_keying.{args.tag or 'v1'}",
        "question": ("Does matching facts by where they came from in the source "
                     "find more comparable pairs, and more disagreements, than "
                     "matching them by name?"),
        "method": ("each extracted figure is anchored to a unique numeric token "
                   "in the case's reference passages, searching the value as "
                   "parsed and at every scale so a mis-scaled extraction still "
                   "anchors. Ambiguous anchors are dropped rather than guessed. "
                   "Facts are then grouped by anchor and by normalized name, and "
                   "the two groupings are compared on how many pairs they "
                   "produce and how many disagreements they expose."),
        "claim_boundary": ("Only facts carrying a figure can be anchored, so "
                           "this says nothing about the rest. An anchor is a "
                           "numeric token, not a verified provenance record — "
                           "two facts can share a token by coincidence, which "
                           "is why ambiguous matches are dropped and why the "
                           "anchor rate is reported beside every result."),
        "tag": args.tag, "models": list(MODELS),
        "by_condition": results,
    }
    (run.dir / "provenance_keying.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print()
    print(f"{'cond':5s} {'figures':>8s} {'anchored':>9s}  "
          f"{'by name: pairs':>15s} {'disagree':>9s}  "
          f"{'by anchor: pairs':>17s} {'disagree':>9s}  {'name-blind':>11s}")
    for arm in arms:
        c = results[arm]
        print(f"{arm:5s} {c['facts_with_a_figure']:8d} {c['anchor_rate']:9.3f}  "
              f"{c['by_name']['comparable']:15d} {c['by_name']['disagreeing']:9d}  "
              f"{c['by_anchor']['comparable']:17d} "
              f"{c['by_anchor']['disagreeing']:9d}  "
              f"{c['disagreements_only_the_anchor_sees']:11d}")
    print("\npairs      = fact groups held by two or more views under that key")
    print("name-blind = disagreements at one source token where the views also "
          "gave the fact different names, so no name key could have compared them")

    for arm in arms:
        examples = results[arm]["examples"]
        if examples:
            print(f"\ncondition {arm}, disagreements name matching could not see:")
            for entry in examples[:5]:
                print(f"  {entry['values']}  ←  {entry['names']}")
            break

    run.finish({"by_condition": {a: {
        "anchor_rate": results[a]["anchor_rate"],
        "name_pairs": results[a]["by_name"]["comparable"],
        "anchor_pairs": results[a]["by_anchor"]["comparable"],
        "name_blind_disagreements": results[a]["disagreements_only_the_anchor_sees"]}
        for a in arms},
        "artifact": str((run.dir / "provenance_keying.json").relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
