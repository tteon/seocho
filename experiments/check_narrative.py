#!/usr/bin/env python3
"""Check that every number in the prose still matches an artifact.

Writing the argument in pieces and merging them later is a good way to work and
a reliable way to publish a stale number. Each document quotes results by hand;
the results then change; nothing notices. The generated `findings/` pages are
safe because their numbers are pulled from artifacts at build time, but the
narrative documents are hand-written and were never checked at all — 29 numbers
in the motivation, 33 in the competency questions, 84 in the spec.

Each document declares, in front matter, which artifacts it is allowed to quote:

    ---
    draws_on:
      - log2026.arm_results.v2
      - log2026.provenance_keying.v1
    cites_published:
      - "43.88"
    ---

Grounding is then checked against **those artifacts only**. The first version of
this check searched the whole 246 MB corpus, which meant almost any three-digit
number matched something and the check could only catch gross drift. Scoping it
to declared sources makes it real, and has a second effect worth as much: the
declaration is a record of where the prose came from, which nothing else in the
repository holds.

A document declaring nothing is checked against the whole corpus and said to be
unscoped, so the weak case is visible rather than silently accepted.

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

NARRATIVE = sorted(
    str(p.relative_to(ROOT))
    for p in (ROOT / "papers/log2026/narrative").glob("*.md")
) + [
    "papers/log2026/COMPETENCY_QUESTIONS.md",
    "papers/log2026/SPEC.md",
    "experiments/preregistration/2026-08-02-second-sweep.md",
    "experiments/preregistration/2026-08-02-scale-up.md",
    "experiments/preregistration/2026-08-02-condition-c-scale.md",
    "experiments/preregistration/2026-08-02-answering.md",
    "papers/log2026/PREREGISTRATION.md",
]
INDEX = ROOT / "experiments/results_index.json"

ARTIFACT_ROOTS = ["outputs", "snapshots"]

# Numbers that come from published work rather than from our runs. Each needs a
# source in the document, and listing them here is the declaration that they are
# not ours — an unlisted foreign number is indistinguishable from a stale one.
# Facts about the input data rather than results of ours. Declared for the same
# reason published figures are: an undeclared one is indistinguishable from a
# stale result.
DATASET = {
    "5703": "FinDER cases in the source parquet",
    "386": "FinDER cases with more than one reference",
    "536": "accepted tickers in the identity registry",
}

CITED = {
    "43.88": "FinanceReasoning — accuracy on convention-dependent constants",
    "0.2": "FinanceReasoning — enforced error margin",
    "0.5": "FinVerBench — rounding-magnitude analysis",
    "2008": "the crisis FIBO was founded after",
    "6611": "real FIBO term count",
    "2127": "FIBO annotation count",
}

# Section numbers, years, list indices, versions, and the confidence level of an
# interval — 95 is a convention, not a measurement, and matching it against
# artifacts would produce noise rather than signal.
STRUCTURAL = re.compile(
    r"^-?(?:[0-9]|1[0-9]|20[0-9]{2}|v[0-9]+|[0-9]\.[0-9]|9[059]|99)$")


def front_matter(text: str) -> tuple[dict[str, list[str]], str]:
    """The declared sources, and the document with them removed."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    head, body = text[3:end], text[end + 4:]
    declared: dict[str, list[str]] = {}
    key = ""
    for line in head.splitlines():
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")) and line.rstrip().endswith(":"):
            key = line.strip().rstrip(":")
            declared[key] = []
        elif key and line.strip().startswith("-"):
            value = line.strip().lstrip("-").strip().strip('"\'')
            if value and value != "[]":
                declared[key].append(value)
    return declared, body


def scoped_corpus(contracts: list[str]) -> str:
    """Only the artifacts a document declared, resolved through the registry."""
    if not INDEX.is_file():
        return ""
    payload = json.loads(INDEX.read_text())
    newest: dict[str, dict[str, Any]] = {}
    for entry in payload.get("results", []):
        current = newest.get(entry["contract"])
        if current is None or entry["modified"] > current["modified"]:
            newest[entry["contract"]] = entry
    blob, missing = [], []
    for contract in contracts:
        entry = newest.get(contract)
        if entry is None:
            missing.append(contract)
            continue
        path = ROOT / entry["path"]
        try:
            blob.append(path.read_text(errors="ignore"))
        except OSError:
            missing.append(contract)
    if missing:
        blob.append("\n".join(f"__MISSING__{c}" for c in missing))
    return "\n".join(blob)


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
    # Prose writes a negative with U+2212, not a hyphen, so an interval bound of
    # -0.0442 was tokenised as 0.0442 and failed to match the artifact holding
    # it. The sign is marked rather than converted to a hyphen: allowing a bare
    # hyphen back into the pattern let "gpt-oss-120b" contribute a 120.
    normalized = re.sub(r"[\u2212\u2013](?=[\d.])", "\u0000", text)
    normalized = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", normalized)
    found = set(re.findall(r"(?<![\w./\-])\x00?\.?\d+(?:\.\d+)?%?", normalized))
    return {t.rstrip("%").replace("\x00", "-") for t in found}


def numbers_of(blob: str) -> set[float]:
    """Every number in the declared artifacts, as numbers.

    Matching prose against artifacts as text was the wrong approach and failed
    on the ordinary case: an artifact holding 0.8667 is quoted as 86.7% in the
    prose, and the string "867" does not occur in "8667". Comparing values with
    a rounding tolerance is what the check meant all along.
    """
    found = set()
    for match in re.finditer(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", blob):
        try:
            found.add(float(match.group(0)))
        except ValueError:
            continue
    return found


def grounded(token: str, values: set[float]) -> bool:
    """Does some artifact hold this number, allowing for how it was rounded?

    The tolerance comes from the token's own precision. A document writing 0.71
    is claiming agreement to two places and should match a stored 0.7083; one
    writing 0.7083 is claiming four and should not match a stored 0.71.
    """
    try:
        value = float(token)
    except ValueError:
        return True
    if value == 0:
        return True
    decimals = len(token.split(".")[1]) if "." in token else 0
    half = 0.5 * (10 ** -decimals)
    for candidate, tolerance in ((value, half),
                                 (value / 100, half / 100),
                                 (value * 100, half * 100)):
        if any(abs(candidate - v) <= tolerance + 1e-12 for v in values):
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    whole = artifact_corpus()
    print(f"whole corpus, used only where a document declares nothing: "
          f"{len(whole) / 1048576:.1f} MB\n")

    report: list[dict[str, Any]] = []
    unscoped = 0
    for relative in NARRATIVE:
        path = ROOT / relative
        if not path.is_file():
            print(f"{relative:52s} MISSING")
            report.append({"document": relative, "status": "missing"})
            continue
        declared, body = front_matter(path.read_text())
        contracts = declared.get("draws_on", [])
        document_cited = set(declared.get("cites_published", []))
        if contracts:
            blob = scoped_corpus(contracts)
            scope = f"{len(contracts)} contracts"
        else:
            blob = whole
            scope = "UNSCOPED"
            unscoped += 1
        values = numbers_of(blob)
        tokens = tokens_in(body)
        checked, cited, structural, loose = [], [], [], []
        for token in sorted(tokens):
            if STRUCTURAL.match(token):
                structural.append(token)
            elif token in CITED or token in DATASET or token in document_cited:
                cited.append(token)
            elif grounded(token, values):
                checked.append(token)
            else:
                loose.append(token)
        status = "ok" if not loose else f"{len(loose)} ungrounded"
        print(f"{relative:52s} {len(tokens):4d} tokens  {scope:14s} {status}")
        if loose:
            print(f"    {', '.join(loose[:18])}"
                  + (f" … +{len(loose) - 18}" if len(loose) > 18 else ""))
        report.append({"document": relative, "tokens": len(tokens),
                       "draws_on": contracts, "scoped": bool(contracts),
                       "grounded": len(checked), "cited": len(cited),
                       "structural": len(structural), "ungrounded": loose,
                       "missing_contracts": [c for c in contracts
                                             if f"__MISSING__{c}" in blob]})

    total_loose = sum(len(r.get("ungrounded", [])) for r in report)
    absent = sorted({c for r in report for c in r.get("missing_contracts", [])})
    print(f"\n{total_loose} numbers across {len(report)} documents match no "
          f"declared artifact and are not declared as cited")
    if unscoped:
        print(f"{unscoped} documents declare no sources and were checked "
              f"against everything, which is the weak case")
    if absent:
        print("\ndeclared but not produced yet — the section is waiting on a run:")
        for contract in absent:
            print(f"  {contract}")
    if CITED:
        print("\ndeclared as coming from published work, not from us:")
        for token, source in sorted(CITED.items()):
            print(f"  {token:8s} {source}")
    if DATASET:
        print("\ndeclared as properties of the input data:")
        for token, source in sorted(DATASET.items()):
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
            "unscoped_documents": unscoped,
            "declared_but_absent_contracts": absent,
            "cited_allowlist": CITED,
            "dataset_allowlist": DATASET,
            "documents": report}, indent=2, ensure_ascii=False) + "\n")
        print(f"\nwrote {args.json}")

    return 1 if (args.strict and total_loose) else 0


if __name__ == "__main__":
    raise SystemExit(main())
