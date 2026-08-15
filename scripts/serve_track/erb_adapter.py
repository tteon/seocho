#!/usr/bin/env python3
"""Turn EnterpriseRAG-Bench questions into the arms format, gold-context only.

The external-validity check for the one result the synthetic set produced with
a mechanism attached: the graph form scores 0 of 4 on negation and absence
while vector scores 4 of 4. ERB has a question type built for exactly that --
`info_not_found`, 20 questions whose correct answer is that the documents do
not answer them -- written by people who had never heard of our experiment.

What this deliberately does NOT do is retrieval. Every arm is built from the
question's own `expected_doc_ids`, so retrieval quality is held constant at
perfect and the only variable is the FORM the same evidence is presented in.
Mixing in a retriever would reintroduce the confound `ADR-0105` recorded: a
graph win could then be retrieval precision rather than representation. That
also means the numbers here are not comparable to published ERB leaderboards,
which measure retrieval and answering together. Say so wherever they appear.

The two selected strata behave differently on purpose:

  info_not_found     zero gold documents by construction. The arms therefore
                     carry the RETRIEVED-BUT-IRRELEVANT neighbourhood rather
                     than nothing, because handing every arm an empty context
                     tests nothing -- the question is whether a form makes the
                     model claim an answer it does not have.
  conflicting_info   two gold documents that disagree. Our synthetic world has
                     no contradictions by construction, so this stratum is not
                     reachable from it at all.

Triples come from two different places, and the difference is the point. ERB
has no gold graph, so a subject-relation-object rendering must be derived.

For a question with gold documents, edges come from `answer_facts` -- the
dataset's own decomposed claims, and what its own scoring uses.

For `info_not_found` that is impossible, and finding out why is what made this
adapter correct. Those items' `answer_facts` is not a fact at all; it is an
instruction -- "The answer must state at some point that the query is not fully
answerable". Extracting edges from it yields nothing, which marked all twenty
items non-comparable and silently deleted the stratum this run exists for.
Their edges are therefore extracted from the neighbourhood DOCUMENTS, which is
also the more faithful simulation: a graph retriever asked an unanswerable
question does not return nothing, it returns whatever edges sit near the query.
Whether the model then refuses or invents is the measurement.

Usage:
    scripts/serve_track/erb_adapter.py \\
        --erb ~/openup/seocho/dataset/enterprise_rag_bench \\
        --types info_not_found conflicting_info \\
        --out outputs/serve_track/erb_questions.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List

SCHEMA_VERSION = 1

# A fact sentence yields an edge when it reads as <subject> <verb phrase> <object>.
# Deliberately conservative: a loose pattern would manufacture edges out of
# ordinary prose and inflate the graph arm with statements it cannot support.
_FACT_EDGE = re.compile(
    r"^(?P<s>[A-Z][\w .,'/()-]{2,60}?)\s+"
    r"(?P<r>is|are|was|were|has|have|must|should|requires?|uses?|supports?|"
    r"provides?|includes?|defaults? to|applies to|belongs to|reports? to|"
    r"depends? on|owns?|sets?|limits?|allows?)\s+"
    r"(?P<o>.{2,120}?)\.?$"
)


def load_documents(erb_root: Path) -> Dict[str, Path]:
    """Map `dsid_<hex>` to its file. Documents are named `dsid_<id>__<slug>.txt`."""
    index: Dict[str, Path] = {}
    for path in (erb_root / "sources").rglob("dsid_*"):
        if not path.is_file():
            continue
        stem = path.name.split("__", 1)[0]
        index.setdefault(stem, path)
    return index


def read_doc(path: Path, max_chars: int) -> str:
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return text[:max_chars]


def facts_to_edges(facts: List[str]) -> List[List[str]]:
    edges: List[List[str]] = []
    for fact in facts:
        match = _FACT_EDGE.match(str(fact).strip())
        if not match:
            continue
        subject = match.group("s").strip(" ,")
        relation = match.group("r").strip().upper().replace(" ", "_")
        obj = match.group("o").strip(" .")
        if subject and obj:
            edges.append([subject, relation, obj])
    return edges


_DOC_EDGE = re.compile(
    r"(?P<s>[A-Z][\w .,'/()-]{2,50}?)\s+"
    r"(?P<r>is|are|was|were|has|have|must|should|requires?|uses?|supports?|"
    r"provides?|includes?|defaults? to|applies to|belongs to|reports? to|"
    r"depends? on|owns?|sets?|limits?|allows?|handles?|runs?|returns?)\s+"
    r"(?P<o>[^.\n]{2,90})"
)


def edges_from_text(texts: List[str], limit: int) -> List[List[str]]:
    """Crude sentence-to-edge extraction over document text.

    Only used where `answer_facts` cannot supply edges. Deliberately shallow --
    the goal is a plausible retrieved subgraph, not a good one. A careful
    extractor would be the wrong instrument here, because the question being
    asked is what a model does when the graph form carries edges that do not
    contain the answer.
    """
    edges: List[List[str]] = []
    seen = set()
    for text in texts:
        for raw in re.split(r"(?<=[.\n])\s+", text):
            match = _DOC_EDGE.match(raw.strip())
            if not match:
                continue
            subject = match.group("s").strip(" ,")
            relation = match.group("r").strip().upper().replace(" ", "_")
            obj = match.group("o").strip(" .,")
            key = (subject.lower(), relation, obj.lower())
            if subject and obj and key not in seen:
                seen.add(key)
                edges.append([subject, relation, obj])
            if len(edges) >= limit:
                return edges
    return edges


def _neighbourhood(doc_index: Dict[str, Path], question: str, limit: int) -> List[str]:
    """Documents to hand an `info_not_found` item, which has no gold docs.

    Picked by filename-slug overlap with the question's rare words. Crude on
    purpose: the point is a plausible near-miss neighbourhood, not good
    retrieval -- if it were good, the item would stop being unanswerable.
    """
    words = {w.lower() for w in re.findall(r"[A-Za-z]{5,}", question)}
    scored = []
    for key, path in doc_index.items():
        slug = path.name.split("__", 1)[-1].lower()
        hits = sum(1 for w in words if w in slug)
        if hits:
            scored.append((hits, key, path))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [str(p) for _, _, p in scored[:limit]]


def build_item(row: Dict[str, Any], doc_index: Dict[str, Path], *,
               max_doc_chars: int, neighbourhood: int,
               edge_limit: int = 40) -> Dict[str, Any]:
    gold_ids = list(row.get("expected_doc_ids") or [])
    paths = [doc_index[i] for i in gold_ids if i in doc_index]
    missing = [i for i in gold_ids if i not in doc_index]

    if not paths and row["question_type"] == "info_not_found":
        paths = [Path(p) for p in _neighbourhood(doc_index, row["question"], neighbourhood)]

    corpus = [read_doc(p, max_doc_chars) for p in paths]
    corpus = [c for c in corpus if c]

    facts = list(row.get("answer_facts") or [])
    edges = facts_to_edges(facts)
    edge_source = "answer_facts"
    if not edges and corpus:
        edges = edges_from_text(corpus, limit=edge_limit)
        edge_source = "document_text"

    return {
        "schema_version": SCHEMA_VERSION,
        "id": row["question_id"],
        "question": row["question"],
        "answer": row.get("gold_answer") or "",
        "excluded": [],
        "corpus": corpus,
        "gold_edges": edges,
        "strata": {
            "stratum": f"ERB_{row['question_type']}",
            "hops": len(edges) or None,
            "dispersion": len(corpus),
            "answer_type": row["question_type"],
            "sources": row.get("source_types") or [],
            "gold_docs_declared": len(gold_ids),
            "gold_docs_missing": len(missing),
            "facts": len(facts),
            "edges": len(edges),
            "edge_source": edge_source,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--erb", type=Path, required=True)
    parser.add_argument("--types", nargs="+",
                        default=["info_not_found", "conflicting_info"])
    parser.add_argument("--out", type=Path,
                        default=Path("outputs/serve_track/erb_questions.jsonl"))
    parser.add_argument("--max-doc-chars", type=int, default=6000,
                        help="cap per document; ERB docs run long and the budget "
                             "is enforced again downstream")
    parser.add_argument("--neighbourhood", type=int, default=3,
                        help="documents to give an info_not_found item")
    args = parser.parse_args()

    doc_index = load_documents(args.erb)
    print(f"indexed {len(doc_index)} documents")

    rows = [json.loads(line) for line in
            (args.erb / "questions.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    selected = [r for r in rows if r["question_type"] in args.types]

    built = [build_item(r, doc_index, max_doc_chars=args.max_doc_chars,
                        neighbourhood=args.neighbourhood) for r in selected]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for item in built:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"wrote {len(built)} items to {args.out}\n")
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for item in built:
        by_type.setdefault(item["strata"]["stratum"], []).append(item)
    for stratum, items in sorted(by_type.items()):
        with_edges = sum(1 for i in items if i["gold_edges"])
        docs = sum(len(i["corpus"]) for i in items) / max(len(items), 1)
        chars = sum(sum(len(c) for c in i["corpus"]) for i in items) / max(len(items), 1)
        print(f"  {stratum:26s} n={len(items):3d}  "
              f"edges_ok={with_edges:3d}  mean_docs={docs:4.1f}  mean_chars={chars:7.0f}")

    unresolved = sum(i["strata"]["gold_docs_missing"] for i in built)
    if unresolved:
        print(f"\nWARN: {unresolved} gold doc ids did not resolve to a file")
    no_edges = [i["id"] for i in built if not i["gold_edges"]]
    if no_edges:
        print(f"\n{len(no_edges)} items yielded no edges from answer_facts and will be "
              f"non-comparable downstream:\n  {', '.join(no_edges[:8])}"
              + (" ..." if len(no_edges) > 8 else ""))


if __name__ == "__main__":
    main()
