"""Derive the graph-vs-vector strata from GraphRAG-Bench, mechanically.

An earlier read of this dataset concluded it "carries no gold triples and no
supporting-fact ids, so hop count and passage dispersion are not derivable".
That was wrong, and the correction matters: the strata that make *when* legible
can be computed from the real corpora, so the synthetic set no longer has to
carry the whole argument.

What is actually there (verified against the local copy, 4,072 items):

  Novel   (2,010)  id source question answer question_type evidence evidence_triple
  Medical (2,062)  id source question answer question_type evidence evidence_relations

`evidence_triple` on the Novel subset holds literal triples —
``(erica vagans, is also known as, Cornish heath).`` — so counting them gives a
hop proxy. And it tracks the dataset's own labels closely enough to trust:

  Fact Retrieval        mean 1.18 triples, 901 of 971 items are EXACTLY one
  Complex Reasoning     mean 2.67, median 2
  Contextual Summarize  mean 3.25, median 3
  Creative Generation   mean 6.16, every item >= 4

Medical has no `evidence_triple` at all (the field is absent; `evidence_relations`
holds prose, not triples), so hops are unavailable there. Its `evidence` is a
`;`-separated statement list, which gives dispersion only — 2.26 / 3.46 / 6.11 /
13.17 statements by the same four types. Medical is therefore usable for the
dispersion axis and must not be pooled with Novel on hops.

The strata this can and cannot supply:

  supplied   hop count (Novel), passage dispersion (both), reasoning type (both)
  NOT supplied  aggregation fan-out, absence/negation, entity ambiguity,
                distractor density
The four it cannot supply are exactly the ones that separate "structure helped"
from "redundancy removal helped" and from "retrieval precision helped", so
`make_question_set.py` still carries S4-S7. Real data where it exists, synthetic
only for what is missing.

Usage:
    python scripts/serve_track/annotate_graphrag_bench.py \\
        --bench-dir ~/openup/_graphrag_benchmark/Datasets/Questions \\
        --out outputs/serve_track/bench_annotated.jsonl
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import statistics
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = 1

# `(subject, relation, object)` with no nested parentheses. Deliberately strict:
# a loose pattern would swallow ordinary parenthetical prose and inflate hops,
# which is the one number this file exists to get right.
_TRIPLE = re.compile(r"\(([^()]+?),([^()]+?),([^()]+?)\)")

# The dataset's own labels, mapped to the axis each one loads. These are the
# dataset's claim, not ours — the derived counts below are what we check it with.
_TYPE_AXIS = {
    "Fact Retrieval": "extractive",
    "Complex Reasoning": "joined",
    "Contextual Summarize": "aggregate_context",
    "Creative Generation": "synthesis",
}


def _load(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    return next(v for v in data.values() if isinstance(v, list))


def _as_text(raw: Any) -> str:
    """Accept both shapes GraphRAG-Bench ships these fields in.

    The HuggingFace release stores `evidence` and `evidence_triple` as LISTS;
    an earlier local copy had them as semicolon-joined strings. This function
    used to accept only the string, silently returning empty for every list —
    so the annotator reported "0 of 2010 carry a derived hop count", which reads
    as a property of the dataset rather than a parse failure, and points the
    reader at exactly the wrong conclusion. Novel in fact yields triples for
    2,002 of 2,010 items.
    """
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (list, tuple)):
        return "; ".join(str(x) for x in raw)
    return ""


def _triples(row: Dict[str, Any]) -> List[List[str]]:
    return [[p.strip() for p in m]
            for m in _TRIPLE.findall(_as_text(row.get("evidence_triple")))]


def _statements(row: Dict[str, Any]) -> List[str]:
    return [s.strip() for s in _as_text(row.get("evidence")).split(";") if s.strip()]


def annotate(row: Dict[str, Any], subset: str) -> Dict[str, Any]:
    triples = _triples(row)
    statements = _statements(row)
    qtype = row.get("question_type", "")

    # hops is None, never 0, when the subset cannot supply it. A zero here would
    # read as "single hop" and quietly merge Medical into the hop analysis.
    hops: Optional[int] = len(triples) if triples else None
    if subset == "medical":
        hops = None

    return {
        "schema_version": SCHEMA_VERSION,
        "id": row.get("id"),
        "subset": subset,
        "source": row.get("source"),
        "question": row.get("question"),
        "answer": row.get("answer"),
        "corpus": statements,
        "strata": {
            "stratum": f"GB_{_TYPE_AXIS.get(qtype, 'unknown')}",
            "question_type": qtype,
            "hops": hops,
            "dispersion": len(statements),
            "answer_type": _TYPE_AXIS.get(qtype, "unknown"),
            "hops_source": "evidence_triple" if hops is not None else "unavailable",
        },
        "gold_edges": triples,
    }


def _summarise(rows: Iterable[Dict[str, Any]]) -> None:
    by_type: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        by_type[row["strata"]["question_type"]].append(row)
    for qtype in sorted(by_type):
        items = by_type[qtype]
        hops = [i["strata"]["hops"] for i in items if i["strata"]["hops"] is not None]
        disp = [i["strata"]["dispersion"] for i in items]
        hop_txt = (
            f"hops mean={statistics.mean(hops):.2f} median={statistics.median(hops):.0f}"
            if hops else "hops unavailable"
        )
        print(f"  {qtype:22s} n={len(items):4d}  {hop_txt}  "
              f"dispersion mean={statistics.mean(disp):.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bench-dir", type=Path,
                        default=Path.home() / "openup/_graphrag_benchmark/Datasets/Questions")
    parser.add_argument("--out", type=Path,
                        default=Path("outputs/serve_track/bench_annotated.jsonl"))
    parser.add_argument("--subsets", nargs="+", default=["novel", "medical"])
    args = parser.parse_args()

    annotated: List[Dict[str, Any]] = []
    for subset in args.subsets:
        path = args.bench_dir / f"{subset}_questions.json"
        if not path.exists():
            print(f"skip {subset}: {path} not found")
            continue
        rows = _load(path)
        got = [annotate(r, subset) for r in rows]
        annotated.extend(got)
        print(f"--- {subset} (n={len(got)})")
        _summarise(got)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for row in annotated:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    with_hops = sum(1 for r in annotated if r["strata"]["hops"] is not None)
    print(f"\nwrote {len(annotated)} items to {args.out}")
    print(f"  {with_hops} carry a derived hop count; "
          f"{len(annotated) - with_hops} carry dispersion only")
    print("Do not pool the two subsets on the hop axis — only Novel supplies it.")


if __name__ == "__main__":
    main()
