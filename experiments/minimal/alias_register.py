#!/usr/bin/env python3
"""Do the filings name FIBO concepts by FIBO's label, or by its synonym?

This decides whether arm D is worth paying for. If filings already use FIBO's
preferred label, the synonym layer adds nothing and D is a wasted arm.

Why this script exists at all: the first pass at this question counted bare word
occurrences and produced numbers that looked decisive and were not. "capital"
occurred 3,340 times, but 1,312 of those were "capital expenditures", which is
an asset outflow and the opposite of the owners' equity concept FIBO declares
"capital" a synonym of. "cash" was inflated the same way by "cash flows" and
"cash equivalents". A single common word carries several senses and a raw count
cannot tell them apart.

So aliases are split by how trustworthy their count is:

  unambiguous   abbreviations (LLC, MCC) and multi-word phrases. One sense in
                practice, so the count means what it says.
  ambiguous     single common words. Counted, reported, and excluded from any
                conclusion, with the compounds they were absorbing shown.

Only the unambiguous group supports a claim. No model is called.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for path in (str(HERE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import arms  # noqa: E402

OUT_ROOT = ROOT / "outputs/minimal"


def is_abbreviation(text: str) -> bool:
    """FIBO's abbreviations are short and mostly upper case: LLC, MCC, PLC."""
    letters = [c for c in text if c.isalpha()]
    if not letters or len(letters) > 6:
        return False
    return sum(c.isupper() for c in letters) >= max(2, len(letters) - 1)


def collect_pairs(fibo: dict) -> list[dict]:
    pairs = []
    for iri, body in fibo["classes"].items():
        if arms.domain_of(iri) not in arms.FIBO_DOMAINS:
            continue
        label_gram = arms.normalize(body["label"])
        if not label_gram:
            continue
        for kind in ("synonym", "abbreviation"):
            for alias in body["annotations"].get(kind, []):
                gram = arms.normalize(alias)
                if not gram or gram == label_gram:
                    continue
                unambiguous = len(gram) > 1 or is_abbreviation(alias)
                pairs.append({
                    "class": body["label"], "label_gram": label_gram,
                    "alias": alias, "alias_gram": gram, "kind": kind,
                    "sense": "unambiguous" if unambiguous else "ambiguous",
                })
    return pairs


def following_words(documents: list[list[str]], word: str,
                    top: int = 4) -> list[tuple[str, int]]:
    """What the corpus puts after a one-word alias, which is what it means."""
    counts: Counter = Counter()
    for doc in documents:
        for i, token in enumerate(doc):
            if token == word:
                counts[doc[i + 1] if i + 1 < len(doc) else ""] += 1
    return counts.most_common(top)


def main() -> int:
    import observe

    run = observe.Run(OUT_ROOT, "alias-register", {"decisive": {
        "fibo_domains": list(arms.FIBO_DOMAINS),
        "sense_split": "abbreviations and multi-word phrases are trusted; "
                       "single common words are reported but excluded",
        "seed": 42}})

    with run.stage("fibo.parse") as out:
        fibo = arms.parse_fibo()
        out["classes"] = len(fibo["classes"])

    with run.stage("corpus.load") as out:
        documents, cases = arms.load_corpus_text()
        out["cases"] = cases
        out["documents"] = len(documents)

    with run.stage("alias.collect") as out:
        pairs = collect_pairs(fibo)
        by_sense = Counter(p["sense"] for p in pairs)
        out["pairs"] = len(pairs)
        out["unambiguous"] = by_sense["unambiguous"]
        out["ambiguous"] = by_sense["ambiguous"]

    with run.stage("alias.count") as out:
        grams = {p["label_gram"] for p in pairs} | {p["alias_gram"] for p in pairs}
        frequency = arms.document_frequency(documents, grams)
        for pair in pairs:
            pair["label_documents"] = frequency.get(pair["label_gram"], 0)
            pair["alias_documents"] = frequency.get(pair["alias_gram"], 0)
        out["phrases_counted"] = len(grams)

    trusted = [p for p in pairs if p["sense"] == "unambiguous"]
    suspect = [p for p in pairs if p["sense"] == "ambiguous"]

    with run.stage("alias.judge", basis="unambiguous pairs only") as out:
        alias_wins = [p for p in trusted
                      if p["alias_documents"] > p["label_documents"]]
        label_absent = [p for p in trusted
                        if p["alias_documents"] > 0 and p["label_documents"] == 0]
        neither = [p for p in trusted
                   if p["alias_documents"] == 0 and p["label_documents"] == 0]
        out["trusted_pairs"] = len(trusted)
        out["alias_more_common_than_label"] = len(alias_wins)
        out["label_never_appears_but_alias_does"] = len(label_absent)
        out["neither_appears"] = len(neither)
        out["share_where_alias_wins"] = (round(len(alias_wins) / len(trusted), 4)
                                         if trusted else 0.0)

    # What the excluded single-word aliases were actually absorbing.
    with run.stage("alias.ambiguity_evidence") as out:
        evidence = []
        for pair in sorted(suspect, key=lambda p: -p["alias_documents"])[:8]:
            word = pair["alias_gram"][0]
            evidence.append({
                "class": pair["class"], "alias": pair["alias"],
                "alias_documents": pair["alias_documents"],
                "followed_by": following_words(documents, word),
            })
        out["examples"] = len(evidence)

    annotated = sum(1 for iri, b in fibo["classes"].items()
                    if arms.domain_of(iri) in arms.FIBO_DOMAINS and b["annotations"])
    in_scope = sum(1 for iri in fibo["classes"]
                   if arms.domain_of(iri) in arms.FIBO_DOMAINS)

    payload = {
        "contract": "log2026.alias_register.v2",
        "question": ("Do the filings name FIBO concepts by FIBO's preferred "
                     "label, or by a synonym FIBO itself declares?"),
        "supersedes": ("log2026.alias_pretest.v1, whose counts conflated word "
                       "senses and overstated the gap"),
        "claim_boundary": ("Only abbreviations and multi-word aliases are "
                           "counted toward the conclusion. Single-word aliases "
                           "such as cash, capital and average absorb compounds "
                           "with different meanings and are reported separately "
                           "as excluded."),
        "documents": len(documents),
        "classes_in_scope": in_scope,
        "classes_with_any_annotation": annotated,
        "annotation_coverage": round(annotated / in_scope, 4) if in_scope else 0.0,
        "pairs": {"total": len(pairs), "trusted": len(trusted),
                  "excluded_ambiguous": len(suspect)},
        "trusted_result": {
            "alias_more_common_than_label": len(alias_wins),
            "label_never_appears_but_alias_does": len(label_absent),
            "neither_appears": len(neither),
        },
        "trusted_top": [
            {"class": p["class"], "label_documents": p["label_documents"],
             "alias": p["alias"], "alias_documents": p["alias_documents"],
             "kind": p["kind"]}
            for p in sorted(alias_wins, key=lambda p: -p["alias_documents"])[:25]
        ],
        "excluded_with_evidence": evidence,
    }
    (run.dir / "alias_register.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print()
    print(f"annotation coverage in scope: {annotated}/{in_scope} "
          f"({annotated / in_scope:.1%})")
    print(f"alias pairs: {len(pairs)} total, {len(trusted)} trusted, "
          f"{len(suspect)} excluded as single common words")
    print(f"of the trusted pairs, the alias is more common than FIBO's label "
          f"in {len(alias_wins)}")
    print()
    print(f"{'FIBO label':34s} {'docs':>6s}   {'alias':24s} {'docs':>6s}")
    for p in sorted(alias_wins, key=lambda p: -p["alias_documents"])[:15]:
        print(f"{p['class'][:34]:34s} {p['label_documents']:6d}   "
              f"{p['alias'][:24]:24s} {p['alias_documents']:6d}")
    print("\nexcluded, and what they were absorbing:")
    for e in evidence[:5]:
        after = ", ".join(f"{w or '<end>'} {n}" for w, n in e["followed_by"])
        print(f"  {e['alias']:12s} {e['alias_documents']:5d} docs   after it: {after}")

    run.finish({
        "annotation_coverage": payload["annotation_coverage"],
        "trusted_pairs": len(trusted),
        "alias_more_common_than_label": len(alias_wins),
        "artifact": str((run.dir / "alias_register.json").relative_to(ROOT)),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
