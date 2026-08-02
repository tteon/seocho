#!/usr/bin/env python3
"""Check that every number in the prose still matches an artifact.

Writing the argument in pieces and merging them later is a good way to work and
a reliable way to publish a stale number. Each document quotes results by hand;
the results then change; nothing notices. The generated `findings/` pages are
safe because their numbers are pulled from artifacts at build time, but the
narrative documents are hand-written and were never checked at all — 29 numbers
in the motivation, 33 in the competency questions, 84 in the spec.

This reads every numeric token out of each narrative document and looks for it
in the artifact corpus: every JSON and Markdown result under `outputs/`, plus
the snapshot manifests. A token that appears nowhere is either stale, mistyped,
or came from the literature rather than from us — and the third case has to be
declared rather than inferred, which is what the citation allowlist is for.

    python3 experiments/check_narrative.py
    python3 experiments/check_narrative.py --strict   non-zero if anything is loose

What it does not do is verify that a number is used to mean the right thing.
A figure can be present in some artifact and still be quoted about the wrong
condition. This catches drift, not misreading.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

NARRATIVE = [
    "papers/log2026/MOTIVATION.md",
    "papers/log2026/COMPETENCY_QUESTIONS.md",
    "papers/log2026/SPEC.md",
    "experiments/PART2_EVALUATION.md",
    "experiments/preregistration/2026-08-02-second-sweep.md",
    "experiments/preregistration/2026-08-02-scale-up.md",
]

ARTIFACT_ROOTS = ["outputs", "snapshots"]

# Numbers that come from published work rather than from our runs. Each needs a
# source in the document, and listing them here is the declaration that they are
# not ours — an unlisted foreign number is indistinguishable from a stale one.
CITED = {
    "43.88": "FinanceReasoning — accuracy on convention-dependent constants",
    "0.2": "FinanceReasoning — enforced error margin",
    "0.5": "FinVerBench — rounding-magnitude analysis",
    "2008": "the crisis FIBO was founded after",
    "6611": "real FIBO term count",
    "2127": "FIBO annotation count",
}

# Section numbers, years, list indices, versions. Matching these against
# artifacts would produce noise, not signal.
STRUCTURAL = re.compile(
    r"^(?:[0-9]|1[0-9]|20[0-9]{2}|v[0-9]+|[0-9]\.[0-9])$")


def artifact_corpus() -> str:
    blob = []
    for base in ARTIFACT_ROOTS:
        root = ROOT / base
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix not in (".json", ".md", ".jsonl"):
                continue
            if path.stat().st_size > 8_000_000:
                continue
            try:
                blob.append(path.read_text(errors="ignore"))
            except OSError:
                continue
    return "\n".join(blob)


def tokens_in(text: str) -> set[str]:
    # Strip code fences and inline code: a Cypher snippet or a command is not a
    # claim, and its numbers are not results.
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"\[[^\]]*\]\([^)]*\)", " ", text)   # links carry URLs, not results
    normalized = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)
    found = set(re.findall(r"(?<![\w.\-/])\.?\d+(?:\.\d+)?%?", normalized))
    return {t.rstrip("%") for t in found}


def grounded(token: str, blob: str) -> bool:
    stem = token.lstrip(".")
    if not stem or set(stem) <= {"0", "."}:
        return True
    for form in {token, stem, "0" + token if token.startswith(".") else token}:
        if form in blob:
            return True
    # A document may round what an artifact stores at full precision: 0.708
    # against 0.70833. Accept a prefix match only when the stem is long enough
    # that the coincidence is unlikely.
    if len(stem.replace(".", "")) >= 3:
        if re.search(r"0?\." + re.escape(stem.lstrip("0.")) + r"\d", blob):
            return True
    # A percentage written as 25% against a stored rate of 0.25.
    try:
        value = float(token)
    except ValueError:
        return False
    for scaled in (value / 100, value * 100):
        text = f"{scaled:g}"
        if text in blob or f"0{text}" in blob:
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    blob = artifact_corpus()
    print(f"artifact corpus: {len(blob) / 1048576:.1f} MB\n")

    report: list[dict[str, Any]] = []
    for relative in NARRATIVE:
        path = ROOT / relative
        if not path.is_file():
            print(f"{relative:52s} MISSING")
            report.append({"document": relative, "status": "missing"})
            continue
        tokens = tokens_in(path.read_text())
        checked, cited, structural, loose = [], [], [], []
        for token in sorted(tokens):
            if STRUCTURAL.match(token):
                structural.append(token)
            elif token in CITED:
                cited.append(token)
            elif grounded(token, blob):
                checked.append(token)
            else:
                loose.append(token)
        status = "ok" if not loose else f"{len(loose)} ungrounded"
        print(f"{relative:52s} {len(tokens):4d} tokens  {status}")
        if loose:
            print(f"    {', '.join(loose[:18])}"
                  + (f" … +{len(loose) - 18}" if len(loose) > 18 else ""))
        report.append({"document": relative, "tokens": len(tokens),
                       "grounded": len(checked), "cited": len(cited),
                       "structural": len(structural), "ungrounded": loose})

    total_loose = sum(len(r.get("ungrounded", [])) for r in report)
    print(f"\n{total_loose} numbers across {len(report)} documents match no "
          f"artifact and are not declared as cited")
    if CITED:
        print("\ndeclared as coming from published work, not from us:")
        for token, source in sorted(CITED.items()):
            print(f"  {token:8s} {source}")

    if args.json:
        args.json.write_text(json.dumps({
            "contract": "seocho.narrative_grounding.v1",
            "question": ("Does every number in the hand-written prose still "
                         "match an artifact?"),
            "claim_boundary": ("Catches drift, not misreading. A number can be "
                               "present in some artifact and still be quoted "
                               "about the wrong condition."),
            "ungrounded_total": total_loose,
            "cited_allowlist": CITED,
            "documents": report}, indent=2, ensure_ascii=False) + "\n")
        print(f"\nwrote {args.json}")

    return 1 if (args.strict and total_loose) else 0


if __name__ == "__main__":
    raise SystemExit(main())
