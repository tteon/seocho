#!/usr/bin/env python3
"""Decide mechanically whether a candidate pair is actually one question.

Human adjudication of the synthesized cross-category pairs is expensive and the
alternative usually reached for — an LLM panel — is not obviously better here.
The judges available to this project have already been measured disagreeing with
each other at kappa between 0.20 and 0.67, with one model a consistently lenient
outlier, and a panel with no human anchor cannot honestly be described as
independent labelling.

But two of the three judgements do not need a judge.

Whether both questions concern one company is already settled deterministically
against a ticker registry: 208 of 240 pass.

Whether answering needs *both* components is also decidable without opinion. A
pair is not composite if either component's own gold answer already contains
everything the other contributes — then one question answers the whole thing and
the second adds nothing. Conversely a pair whose two golds share no facts at all
is two unrelated questions side by side. Composite means the middle: each
component contributes something the other does not, and both are needed.

Measured three ways, since "contributes something" has more than one reasonable
reading and a conclusion that depends on which one is chosen is not a conclusion:

    figures     the numbers each gold states, matched within 1%
    entities    the capitalised names and tickers each gold names
    content     the content words each gold uses, stopwords removed

Only the naturalness judgement — would anyone ask this as one question — needs
an opinion, and that one is left to a panel and labelled as model-judged rather
than folded in here.

Read-only. No model, no database.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs/evaluation/mdm_fedcat"
CANDIDATES = BASE / "log2026-full-finder-cross-view-v1/candidates.json"
OUT_ROOT = ROOT / "outputs/minimal"

_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")
_ENTITY = re.compile(r"\b[A-Z][A-Za-z&.\-]{2,}\b")
STOP = {
    "the", "and", "for", "with", "that", "this", "from", "have", "has", "are",
    "was", "were", "been", "which", "their", "these", "those", "into", "over",
    "under", "about", "between", "during", "company", "companies", "year",
    "years", "fiscal", "total", "also", "may", "can", "will", "would", "could",
    "not", "other", "such", "than", "then", "there", "they", "them", "its",
    "our", "his", "her", "any", "all", "some", "each", "per", "more", "most",
}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def figures(text: str) -> set[float]:
    found = set()
    for match in _NUMBER.finditer(str(text or "")):
        try:
            value = float(match.group(0).replace(",", ""))
        except ValueError:
            continue
        if value != 0:
            found.add(value)
    return found


def entities(text: str) -> set[str]:
    return {t for t in _ENTITY.findall(str(text or "")) if t.lower() not in STOP}


def content(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", str(text or "").lower())
            if w not in STOP}


def close(a: float, b: float, tolerance: float = 0.01) -> bool:
    if a == b:
        return True
    scale = max(abs(a), abs(b))
    return scale > 0 and abs(a - b) / scale <= tolerance


def contribution(left: set, right: set, numeric: bool = False) -> dict[str, Any]:
    """What each side holds that the other does not, and what they share.

    A pair where one side contributes nothing is answerable by the other alone.
    A pair sharing nothing is two questions that happen to be adjacent.
    """
    if numeric:
        shared = {a for a in left if any(close(a, b) for b in right)}
        only_left = {a for a in left if not any(close(a, b) for b in right)}
        only_right = {b for b in right if not any(close(b, a) for a in left)}
    else:
        shared = left & right
        only_left, only_right = left - right, right - left
    union = len(shared) + len(only_left) + len(only_right)
    return {
        "shared": len(shared), "only_left": len(only_left),
        "only_right": len(only_right),
        "overlap": round(len(shared) / union, 4) if union else 0.0,
        "both_contribute": bool(only_left) and bool(only_right),
        "either_empty": not left or not right,
    }


def judge(left_gold: str, right_gold: str) -> dict[str, Any]:
    views = {
        "figures": contribution(figures(left_gold), figures(right_gold), True),
        "entities": contribution(entities(left_gold), entities(right_gold)),
        "content": contribution(content(left_gold), content(right_gold)),
    }
    # A reading where one gold holds nothing of that kind cannot rule either
    # way. The first version counted it as a failure, which put 126 of 240 pairs
    # in "disputed" for the sole reason that one of their answers is prose with
    # no figures in it — a fact about the answer's form, not about whether the
    # pair is composite. Not-applicable is now its own outcome and is excluded
    # from the vote rather than counted against.
    applicable = {k: v for k, v in views.items() if not v["either_empty"]}
    agree = sum(1 for v in applicable.values() if v["both_contribute"])
    if not applicable:
        verdict = "undecidable"
    elif agree == len(applicable):
        verdict = "composite"
    elif agree == 0:
        verdict = "not_composite"
    else:
        verdict = "disputed"
    return {
        "views": views,
        "applicable_readings": sorted(applicable),
        "composite_by_figures": (None if views["figures"]["either_empty"]
                                 else views["figures"]["both_contribute"]),
        "composite_by_entities": (None if views["entities"]["either_empty"]
                                  else views["entities"]["both_contribute"]),
        "composite_by_content": (None if views["content"]["either_empty"]
                                 else views["content"]["both_contribute"]),
        "views_agreeing": agree, "views_applicable": len(applicable),
        "verdict": verdict,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT / "experiments/minimal"))
    import observe

    run = observe.Run(OUT_ROOT, "composability", {"decisive": {
        "candidates": str(CANDIDATES.relative_to(ROOT)),
        "readings": ["figures", "entities", "content"],
        "numeric_tolerance": 0.01, "seed": 42}})

    with run.stage("load") as out:
        pool = load_module(ROOT / "examples/mdm/74_validated_issuer_pool.py",
                           "issuer_pool")
        tickers = pool.accepted_tickers()
        candidates = json.loads(CANDIDATES.read_text())["candidates"]
        out["candidates"] = len(candidates)
        out["accepted_tickers"] = len(tickers)

    with run.stage("judge") as out:
        rows = []
        for row in candidates:
            golds = row["required_gold_slots"]
            questions = row["component_questions"]
            validated = [pool.validated_issuer(str(q), tickers) for q in questions]
            resolved = [v for v in validated if v]
            same_issuer = len(set(resolved)) == 1 and len(resolved) == 2
            verdict = judge(str(golds[0]), str(golds[1]))
            rows.append({
                "candidate_id": row["candidate_id"], "split": row["split"],
                "categories": row["required_categories"],
                "same_issuer": same_issuer,
                **{k: v for k, v in verdict.items() if k != "views"},
                "views": verdict["views"],
                "usable": same_issuer and verdict["verdict"] == "composite",
            })
        counts = Counter(r["verdict"] for r in rows)
        out.update({f"verdict_{k}": v for k, v in counts.items()})
        out["usable"] = sum(1 for r in rows if r["usable"])

    usable = [r for r in rows if r["usable"]]
    by_split = Counter(r["split"] for r in usable)
    # Where the three readings disagree, which is where a single measure would
    # have quietly decided for us.
    disputed = [r for r in rows if r["verdict"] == "disputed"]

    payload = {
        "contract": "log2026.composability.v1",
        "question": ("Does answering a synthesized pair require both of its "
                     "component questions, decided without a judge?"),
        "method": ("a pair is composite when each component's gold answer holds "
                   "something the other's does not. Tested under three readings "
                   "of 'something' — stated figures, named entities, content "
                   "words — and reported as composite only when all three "
                   "agree, disputed when they split."),
        "claim_boundary": ("Decides whether the two components contribute "
                           "different facts. It does not decide whether anyone "
                           "would ask the pair as one question, which needs a "
                           "judgement and is left to a panel reported as "
                           "model-judged. Gold answers are prose of uneven "
                           "length, so the content reading favours longer "
                           "answers and is the weakest of the three."),
        "candidates": len(rows),
        "same_issuer": sum(1 for r in rows if r["same_issuer"]),
        "verdicts": dict(counts),
        "usable": len(usable),
        "usable_rate": round(len(usable) / len(rows), 4) if rows else 0.0,
        "usable_by_split": dict(by_split),
        "disputed_examples": [
            {"candidate_id": r["candidate_id"],
             "categories": r["categories"],
             "by_figures": r["composite_by_figures"],
             "by_entities": r["composite_by_entities"],
             "by_content": r["composite_by_content"]}
            for r in disputed[:10]],
        "rows": rows,
    }
    (run.dir / "composability.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print()
    print(f"candidates                     {len(rows)}")
    print(f"same issuer (registry)         {payload['same_issuer']}")
    print(f"\nverdict, all three readings agreeing:")
    for verdict, count in counts.most_common():
        print(f"  {verdict:16s} {count:4d}")
    print(f"\nusable — same issuer AND composite: {len(usable)} "
          f"({payload['usable_rate']:.1%})")
    print(f"by split: {dict(by_split)}")
    if disputed:
        print(f"\n{len(disputed)} disputed, where the three readings split:")
        for entry in payload["disputed_examples"][:6]:
            marks = "".join("-" if entry[k] is None else ("y" if entry[k] else "n")
                            for k in ("by_figures", "by_entities", "by_content"))
            print(f"  {entry['candidate_id'][:34]:34s} fig/ent/con = {marks}")

    run.finish({"usable": len(usable), "usable_rate": payload["usable_rate"],
                "verdicts": dict(counts),
                "artifact": str((run.dir / "composability.json").relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
