#!/usr/bin/env python3
"""Split the questions by what an ontology would have to supply to answer them.

An ontology carries three separable things, and a question can need any of them:
a vocabulary, so a term in the question can be matched to a differently-worded
term in the filing; a notion of quantity, so figures can be compared; and a
structure, so facts in different places can be joined. Lumping those together
produces a single accuracy number that cannot say which of the three did any
work.

Two of the three are already labelled by the dataset, which is better than a
taxonomy we invented:

    numeric      `type` is an arithmetic operation — Division, Multiplication,
                 Subtract, Addition. The dataset states, without our choosing
                 it, which questions need a figure compared
    structural   `type` is Compositional, or the case has more than one
                 reference, so an answer has to combine parts

The third has no column and has to be derived, which is the interesting one.
A question is **terminology-dependent** when the words it uses are not the words
the filing uses, and only a declared synonym bridges them: the question says
EBITDA and the filing spells the expansion out, or the question says parent
company and the filing says total controlling interest party. Without the
vocabulary that bridge does not exist, and no amount of retrieval crosses it.

That is measurable from what is already here — FIBO's alias pairs, the question
text, and the case's own reference text — and unlike the other two it is
available in all eight categories, because it needs no annotation.

The three are reported as overlapping sets rather than a partition. A question
can be terminological and numeric at once, and forcing a split would invent
structure the questions do not have.

No model is called.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for path in (str(ROOT / "experiments/minimal"), str(HERE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import arms as arms_mod  # noqa: E402

OUT_ROOT = ROOT / "outputs/minimal"
ARITHMETIC = {"Division", "Multiplication", "Subtract", "Subtraction",
              "Addition"}


def load_cases() -> list[dict[str, Any]]:
    spec = importlib.util.spec_from_file_location(
        "finder_index", ROOT / "examples/mdm/11_index_providers.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load_cases_full(seed=42)


def raw_labels() -> dict[str, dict[str, Any]]:
    """`type` and `reasoning` straight from the parquet, which the loader drops."""
    import pandas as pd

    df = pd.read_parquet(
        ROOT / ".seocho/datasets/finder/data/train-00000-of-00001.parquet")
    return {str(r["_id"]): {"type": str(r["type"]),
                            "reasoning": str(r["reasoning"]) == "True"}
            for _, r in df.iterrows()}


def alias_pairs(class_limit: int) -> list[tuple[str, str, str]]:
    """(label, alias, kind) for FIBO classes, trustworthy ones only.

    Single common words are excluded for the same reason the register
    measurement excludes them: "capital" absorbs "capital expenditures" and its
    presence proves nothing about vocabulary.
    """
    fibo = arms_mod.parse_fibo()
    pairs = []
    for iri, body in fibo["classes"].items():
        if arms_mod.domain_of(iri) not in arms_mod.FIBO_DOMAINS:
            continue
        label = body["label"]
        for kind in ("synonym", "abbreviation"):
            for alias in body["annotations"].get(kind, []):
                gram = arms_mod.normalize(alias)
                if not gram or gram == arms_mod.normalize(label):
                    continue
                letters = [c for c in alias if c.isalpha()]
                short_upper = (letters and len(letters) <= 6
                               and sum(c.isupper() for c in letters)
                               >= max(2, len(letters) - 1))
                if len(gram) == 1 and not short_upper:
                    continue
                pairs.append((label, alias, kind))
    return pairs


def contains(text: str, phrase: str) -> bool:
    """Word-boundary containment, so LLC does not match COLLECTION."""
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])",
                     text, re.IGNORECASE) is not None


def terminology_bridge(question: str, references: str,
                       pairs: list[tuple[str, str, str]]) -> list[dict[str, str]]:
    """Where the question's word and the filing's word differ but FIBO links them."""
    found = []
    for label, alias, kind in pairs:
        q_label, q_alias = contains(question, label), contains(question, alias)
        r_label, r_alias = contains(references, label), contains(references, alias)
        if q_alias and not q_label and r_label and not r_alias:
            found.append({"question_says": alias, "filing_says": label,
                          "kind": kind, "direction": "alias_to_label"})
        elif q_label and not q_alias and r_alias and not r_label:
            found.append({"question_says": label, "filing_says": alias,
                          "kind": kind, "direction": "label_to_alias"})
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--class-limit", type=int, default=70)
    ap.add_argument("--limit", type=int, default=0, help="0 for every case")
    args = ap.parse_args()

    import observe

    run = observe.Run(OUT_ROOT, "question-axes", {"decisive": {
        "axes": ["terminology", "numeric", "structural"],
        "numeric_from": "dataset `type` column, arithmetic values",
        "structural_from": "dataset `type` Compositional, or n_refs >= 2",
        "terminology_from": ("FIBO alias pairs bridging a question's wording "
                             "and the filing's, trustworthy pairs only"),
        "class_limit": args.class_limit, "seed": 42}})

    with run.stage("load") as out:
        cases = load_cases()
        if args.limit:
            cases = cases[:args.limit]
        labels = raw_labels()
        pairs = alias_pairs(args.class_limit)
        out["cases"] = len(cases)
        out["alias_pairs"] = len(pairs)

    with run.stage("classify", cases=len(cases)) as out:
        rows = []
        for case in cases:
            meta = labels.get(case["case_id"], {})
            kind = meta.get("type", "None")
            references = " ".join(case["references"])
            bridges = terminology_bridge(str(case["query"]), references, pairs)
            rows.append({
                "case_id": case["case_id"], "category": case["category"],
                "type": kind, "reasoning": meta.get("reasoning", False),
                "n_refs": len(case["references"]),
                "numeric": kind in ARITHMETIC,
                "structural": kind == "Compositional" or len(case["references"]) >= 2,
                "terminology": bool(bridges),
                "bridges": bridges[:4],
            })
        out["numeric"] = sum(1 for r in rows if r["numeric"])
        out["structural"] = sum(1 for r in rows if r["structural"])
        out["terminology"] = sum(1 for r in rows if r["terminology"])

    with run.stage("overlap") as out:
        combos: Counter = Counter()
        for row in rows:
            key = tuple(sorted(a for a in ("terminology", "numeric", "structural")
                               if row[a])) or ("none",)
            combos[" + ".join(key)] += 1
        by_category: dict[str, dict[str, int]] = defaultdict(
            lambda: {"n": 0, "terminology": 0, "numeric": 0, "structural": 0})
        for row in rows:
            cell = by_category[row["category"]]
            cell["n"] += 1
            for axis in ("terminology", "numeric", "structural"):
                cell[axis] += int(row[axis])
        out["combinations"] = dict(combos.most_common())

    bridge_terms = Counter()
    for row in rows:
        for bridge in row["bridges"]:
            bridge_terms[f"{bridge['question_says']} ← {bridge['filing_says']}"] += 1

    payload = {
        "contract": "log2026.question_axes.v1",
        "question": ("Which questions need a vocabulary, which need a figure "
                     "compared, and which need parts joined?"),
        "method": ("numeric and structural taken from the dataset's own `type` "
                   "column and reference count; terminology derived from FIBO "
                   "alias pairs that bridge a question's wording and the "
                   "filing's, using only pairs whose counts can be trusted"),
        "claim_boundary": ("The terminology axis detects a lexical bridge, not "
                           "that answering requires crossing it — a question can "
                           "use a synonym the filing does not while the answer "
                           "sits elsewhere. Numeric and structural are only "
                           "annotated in Financials and Company overview, so "
                           "their absence elsewhere means unannotated rather "
                           "than absent, and no rate should be read across all "
                           "eight categories."),
        "cases": len(rows), "alias_pairs_used": len(pairs),
        "totals": {axis: sum(1 for r in rows if r[axis])
                   for axis in ("terminology", "numeric", "structural")},
        # Shares as well as counts. Four times now the grounding check has
        # caught prose quoting a proportion the artifact held only the
        # numerator and denominator for.
        "shares": {axis: round(sum(1 for r in rows if r[axis]) / len(rows), 4)
                   for axis in ("terminology", "numeric", "structural")},
        "combinations": dict(combos.most_common()),
        "combination_shares": {k: round(v / len(rows), 4)
                               for k, v in combos.most_common()},
        "by_category": {k: dict(v) for k, v in sorted(by_category.items())},
        "most_common_bridges": dict(bridge_terms.most_common(15)),
        "rows": rows,
    }
    (run.dir / "question_axes.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    total = len(rows)
    print()
    print(f"{total:,} questions, {len(pairs)} trustworthy alias pairs\n")
    print(f"{'axis':14s} {'cases':>7s} {'share':>7s}")
    for axis in ("terminology", "numeric", "structural"):
        count = payload["totals"][axis]
        print(f"{axis:14s} {count:7d} {count / total:7.1%}")
    print("\noverlap, as sets rather than a partition:")
    for combo, count in combos.most_common():
        print(f"  {combo:44s} {count:5d}")
    print(f"\n{'category':20s} {'n':>5s} {'term':>6s} {'num':>5s} {'struct':>7s}")
    for category, cell in payload["by_category"].items():
        print(f"{category:20s} {cell['n']:5d} {cell['terminology']:6d} "
              f"{cell['numeric']:5d} {cell['structural']:7d}")
    if bridge_terms:
        print("\nthe bridges the vocabulary would have to cross:")
        for term, count in bridge_terms.most_common(8):
            print(f"  {count:5d}  {term}")

    run.finish({"totals": payload["totals"],
                "combinations": dict(combos.most_common(6)),
                "artifact": str((run.dir / "question_axes.json").relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
