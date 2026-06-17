#!/usr/bin/env python3
"""Dataset loaders for the RAG-vs-GraphRAG replication (arXiv 2502.11371).

Loads the two phase-1 (already-downloaded) datasets into the SAME case-dict
contract the FinDER runners consume (``finder_4arm_sample.load_sample``):

    {case_id, slice, category, type, n_refs, query, expected_answer, references[]}

so the open-domain vector / graph / hybrid lanes are interchangeable with the
existing harness.

Phase-1 datasets (downloaded under ``dataset/robustness/``):
  - HotPotQA dev-distractor  -> slices HP_bridge, HP_comparison      (metric: token-F1/EM)
  - NovelQA-proxy (GraphRAG-Bench `novel`, 4-class)
        -> slices NV_Fact_Retrieval (RAG control), NV_Complex_Reasoning,
           NV_Contextual_Summarize  (Creative Generation EXCLUDED — not scorable)

Fairness / honesty (CLAUDE.md §20):
  - HotPotQA references = ALL 10 distractor-setting paragraphs (2 gold + 8
    distractors), so the graph build and the vector index read byte-identical
    source (no oracle-evidence asymmetry — red-team C3).
  - NovelQA references = the full source novel text (shared-corpus, chunked at
    retrieval time); `evidence`/`evidence_triple` kept aside for later
    extraction-recall instrumentation (red-team C2).
  - Stratified sampling, fixed seed (default 42), deterministic order.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
ROBUSTNESS = ROOT / "dataset" / "robustness"

HOTPOT_JSONL = ROBUSTNESS / "hotpotqa" / "hotpot_dev_distractor.json"
NOVEL_QUESTIONS = ROBUSTNESS / "graphrag_bench" / "Questions" / "novel_questions.json"
NOVEL_CORPUS = ROBUSTNESS / "graphrag_bench" / "Corpus" / "novel.json"

# NovelQA-proxy types we score (Creative Generation excluded — open-ended, no gold)
NOVELQA_SCORABLE = {"Fact Retrieval", "Complex Reasoning", "Contextual Summarize"}


def _stratified(cases: list[dict], n_per_slice: int, seed: int) -> list[dict]:
    """Deterministic stratified sample mirroring finder_4arm_sample.load_sample.

    Group by ``slice``, sort each group by ``case_id``, sample ``n_per_slice``
    with a per-run seeded RNG, then concatenate in sorted slice order.
    """
    by_slice: dict[str, list[dict]] = {}
    for c in cases:
        by_slice.setdefault(c["slice"], []).append(c)
    out: list[dict] = []
    for slice_tag in sorted(by_slice):
        group = sorted(by_slice[slice_tag], key=lambda c: str(c["case_id"]))
        take = min(n_per_slice, len(group))
        rng = random.Random(seed)
        out.extend(sorted(rng.sample(group, take), key=lambda c: str(c["case_id"])))
    return out


def _para_text(title: str, sentences: list[str]) -> str:
    return f"{title}: {' '.join(s.strip() for s in sentences).strip()}"


def load_hotpotqa(n_per_slice: int = 0, seed: int = 42) -> list[dict]:
    """HotPotQA dev-distractor -> case dicts. n_per_slice=0 loads all."""
    if not HOTPOT_JSONL.is_file():
        raise SystemExit(f"Missing HotPotQA at {HOTPOT_JSONL}")
    cases: list[dict] = []
    with open(HOTPOT_JSONL) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            ctx = row["context"]
            # all 10 paragraphs (gold + distractors) — symmetric provenance
            refs = [_para_text(t, s) for t, s in zip(ctx["title"], ctx["sentences"])]
            cases.append({
                "case_id": row["id"],
                "slice": f"HP_{row['type']}",
                "category": "hotpotqa",
                "type": row["type"],
                "n_refs": len(row["supporting_facts"]["title"]),
                "query": row["question"],
                "expected_answer": row["answer"],
                "references": refs,
                # sidecar (not consumed by the shared contract; for recall instrumentation)
                "_supporting_titles": list(dict.fromkeys(row["supporting_facts"]["title"])),
                "_level": row.get("level"),
            })
    return _stratified(cases, n_per_slice, seed) if n_per_slice else cases


def load_novelqa_proxy(n_per_slice: int = 0, seed: int = 42) -> list[dict]:
    """GraphRAG-Bench `novel` (NovelQA proxy) -> case dicts. Excludes Creative Generation."""
    if not NOVEL_QUESTIONS.is_file() or not NOVEL_CORPUS.is_file():
        raise SystemExit(f"Missing NovelQA-proxy at {NOVEL_QUESTIONS} / {NOVEL_CORPUS}")
    corpus = {c["corpus_name"]: c["context"] for c in json.load(open(NOVEL_CORPUS))}
    questions = json.load(open(NOVEL_QUESTIONS))
    cases: list[dict] = []
    for q in questions:
        qtype = q["question_type"]
        if qtype not in NOVELQA_SCORABLE:
            continue
        novel_text = corpus.get(q["source"])
        if not novel_text:
            continue
        cases.append({
            "case_id": q["id"],
            "slice": f"NV_{qtype.replace(' ', '_')}",
            "category": "novelqa_proxy",
            "type": qtype,
            "n_refs": 1,
            "query": q["question"],
            "expected_answer": q["answer"],
            "references": [novel_text],
            "_source_novel": q["source"],
            "_gold_evidence": q.get("evidence"),
            "_gold_triple": q.get("evidence_triple"),
        })
    return _stratified(cases, n_per_slice, seed) if n_per_slice else cases


LOADERS: dict[str, Callable[..., list[dict]]] = {
    "hotpotqa": load_hotpotqa,
    "novelqa": load_novelqa_proxy,
}


def load_dataset(name: str, n_per_slice: int = 0, seed: int = 42) -> list[dict]:
    if name not in LOADERS:
        raise SystemExit(f"Unknown dataset '{name}'. Known: {sorted(LOADERS)}")
    return LOADERS[name](n_per_slice=n_per_slice, seed=seed)


if __name__ == "__main__":
    # $0 self-check: print slice distribution + one example per dataset.
    for name in ("hotpotqa", "novelqa"):
        allc = load_dataset(name, n_per_slice=0)
        dist: dict[str, int] = {}
        for c in allc:
            dist[c["slice"]] = dist.get(c["slice"], 0) + 1
        print(f"\n=== {name}: {len(allc)} cases ===")
        for s, n in sorted(dist.items()):
            print(f"  {s:<28} {n}")
        ex = load_dataset(name, n_per_slice=1, seed=42)[0]
        print(f"  example {ex['case_id']} [{ex['slice']}] refs={len(ex['references'])} "
              f"n_refs={ex['n_refs']}")
        print(f"    q: {ex['query'][:80]}")
        print(f"    a: {str(ex['expected_answer'])[:60]}")
