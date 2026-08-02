#!/usr/bin/env python3
"""Check the synthesis before the paper leans on it.

FinDER has no natively multi-source question. Every multi-reference case is one
issuer's filing read in two places, and the corpus has no issuer column at all.
So the cross-category questions this study needs had to be constructed: two
single-reference questions from different categories, paired because a regex
found the same ticker in both.

That is a defensible thing to do and an indefensible thing to do quietly. The
pairing rests on one step — inferring an issuer from question text with
"the last uppercase two-to-five letter token" — and that step has no accuracy
figure attached to it anywhere. This produces one.

Three checks, in increasing severity:

    resolvable   does a validated ticker exist in the question at all? The
                 registry holds 536 accepted tickers, and a question naming
                 none cannot be attributed to any issuer
    agreement    does the regex used to build the candidates pick the same
                 issuer the registry validates? Where they differ the regex
                 picked something that is not a company in our universe
    same issuer  do BOTH questions in a pair resolve to the SAME validated
                 issuer? A pair that does not is two companies stapled
                 together, and no labelling can rescue it

The third is the one that decides whether a candidate is usable. It is checked
against the registry rather than against the regex that produced the pair, so
the check is independent of the thing it checks.

Read-only. No model, no database.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs/evaluation/mdm_fedcat"
CANDIDATES = BASE / "log2026-full-finder-cross-view-v1/candidates.json"
OUT_ROOT = ROOT / "outputs/minimal"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT / "experiments/minimal"))
    import observe

    run = observe.Run(OUT_ROOT, "validate-synthesis", {"decisive": {
        "candidates": str(CANDIDATES.relative_to(ROOT)),
        "ticker_registry": "log2026-clean-entity-network-v1 identity_registry.accepted",
        "seed": 42}})

    with run.stage("load") as out:
        pool = load_module(ROOT / "examples/mdm/74_validated_issuer_pool.py",
                           "issuer_pool")
        tickers = pool.accepted_tickers()
        candidates = json.loads(CANDIDATES.read_text())["candidates"]
        cases = {c["case_id"]: c for c in load_module(
            ROOT / "examples/mdm/11_index_providers.py",
            "finder_index").load_cases_full(seed=42)}
        out["accepted_tickers"] = len(tickers)
        out["candidates"] = len(candidates)
        out["cases"] = len(cases)

    with run.stage("issuer") as out:
        rows = []
        for row in candidates:
            questions = row["component_questions"]
            legacy = row["issuer"]
            validated = [pool.validated_issuer(str(q), tickers) for q in questions]
            resolved = [v for v in validated if v]
            same = len(set(resolved)) == 1 and len(resolved) == 2
            rows.append({
                "candidate_id": row["candidate_id"], "split": row["split"],
                "categories": row["required_categories"],
                "regex_issuer": legacy,
                "validated": validated,
                "both_resolvable": len(resolved) == 2,
                "regex_matches_registry": bool(resolved) and legacy in set(resolved),
                "same_validated_issuer": same,
            })
        both = sum(1 for r in rows if r["both_resolvable"])
        agree = sum(1 for r in rows if r["regex_matches_registry"])
        same = sum(1 for r in rows if r["same_validated_issuer"])
        out["both_questions_resolvable"] = f"{both}/{len(rows)}"
        out["regex_matches_registry"] = f"{agree}/{len(rows)}"
        out["same_validated_issuer"] = f"{same}/{len(rows)}"

    with run.stage("distribution") as out:
        # How the synthesized composites sit against the corpus they came from.
        # A synthesis that looks nothing like the real thing is a different task
        # wearing the dataset's name.
        component_ids = {cid for row in candidates
                         for cid in row["component_case_ids"]}
        component_lengths = [len(str(cases[c]["query"]).split())
                             for c in component_ids if c in cases]
        corpus_lengths = [len(str(c["query"]).split()) for c in cases.values()]
        pair_counts = Counter(" + ".join(sorted(r["required_categories"]))
                              for r in candidates)
        corpus_categories = Counter(c["category"] for c in cases.values())
        used_categories = Counter(cat for r in candidates
                                  for cat in r["required_categories"])
        out["component_cases"] = len(component_ids)
        out["median_words_component"] = statistics.median(component_lengths)
        out["median_words_corpus"] = statistics.median(corpus_lengths)
        out["distinct_category_pairs"] = len(pair_counts)

    usable = [r for r in rows if r["same_validated_issuer"]]
    by_split = Counter(r["split"] for r in usable)
    failures = Counter()
    for row in rows:
        if row["same_validated_issuer"]:
            continue
        if not row["both_resolvable"]:
            failures["a question names no accepted ticker"] += 1
        else:
            failures["the two questions name different companies"] += 1

    payload = {
        "contract": "log2026.synthesis_validation.v1",
        "question": ("Do the synthesized cross-category pairs actually concern "
                     "one company, checked against a ticker registry rather "
                     "than against the regex that built them?"),
        "method": ("each pair's two component questions resolved independently "
                   "against 536 accepted tickers; a pair is usable only when "
                   "both resolve and resolve to the same issuer"),
        "claim_boundary": ("Validates that a pair concerns one company. It does "
                           "not establish that the pair forms a question anyone "
                           "would ask, which is what the human adjudication "
                           "packet is for, and it cannot make a synthesized "
                           "question native — FinDER contains none."),
        "candidates": len(rows),
        "both_questions_resolvable": both,
        # The rate as well as the count. The prose quotes a percentage, and the
        # grounding check found it unsupported because the artifact stored only
        # the numerator and denominator — an artifact should hold what the paper
        # says, not the ingredients for it.
        "both_resolvable_rate": round(both / len(rows), 4) if rows else 0.0,
        "regex_matches_registry": agree,
        "regex_accuracy": round(agree / len(rows), 4) if rows else 0.0,
        "same_validated_issuer": same,
        "usable_rate": round(same / len(rows), 4) if rows else 0.0,
        "failure_reasons": dict(failures),
        "usable_by_split": dict(by_split),
        "category_pairs": dict(pair_counts),
        "corpus_category_counts": dict(corpus_categories),
        "categories_used_by_candidates": dict(used_categories),
        "question_length_words": {
            "component_median": statistics.median(component_lengths),
            "corpus_median": statistics.median(corpus_lengths)},
        "rows": rows,
    }
    (run.dir / "synthesis_validation.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print()
    print(f"candidates                              {len(rows)}")
    print(f"both questions name an accepted ticker  {both} "
          f"({both / len(rows):.1%})")
    print(f"regex agrees with the registry          {agree} "
          f"({agree / len(rows):.1%})")
    print(f"both resolve to the SAME issuer         {same} "
          f"({same / len(rows):.1%})   <- usable")
    print("\nwhy the rest fail:")
    for reason, count in failures.most_common():
        print(f"  {count:4d}  {reason}")
    print(f"\nusable by split: {dict(by_split)}")
    print(f"question length, words: component median "
          f"{statistics.median(component_lengths):.0f}, corpus median "
          f"{statistics.median(corpus_lengths):.0f}")

    run.finish({"usable_rate": payload["usable_rate"],
                "regex_accuracy": payload["regex_accuracy"],
                "usable": same,
                "artifact": str((run.dir / "synthesis_validation.json").relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
