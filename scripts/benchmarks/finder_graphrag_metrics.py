#!/usr/bin/env python3
"""GraphRAG-style reference-free answer-quality metrics, per ontology arm.

Implements the head-to-head LLM evaluation from Microsoft GraphRAG
("From Local to Global: A Graph RAG Approach to Query-Focused Summarization"):
each answer pair is judged on a dimension and the winner counted, yielding a
**win-rate**. These metrics are REFERENCE-FREE (no gold answer) — they measure
the richness of the response, complementing our gold-grounded correctness judge.

Dimensions (verbatim intent from the GraphRAG paper):
  - comprehensiveness: How much detail does the answer provide to cover all
    aspects and details of the question?
  - diversity: How varied and rich is the answer in providing different
    perspectives and insights on the question?

Contrast: for every case we compare each ontology arm's graph / vector_graph
answer against the SAME case's vector-RAG answer (the GraphRAG paper's exact
"graph RAG vs vector RAG" setup). A/B position is flipped deterministically per
comparison to cancel position bias (single call per comparison).

Usage:
  python scripts/benchmarks/finder_graphrag_metrics.py \
    --inputs-vector "outputs/evaluation/finder_vector_arm/<run>/partial/*.json" \
    --inputs-graph  "outputs/evaluation/finder_4arm_sample/<run>/partial/*.json"
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples.finder.lib import bench_common as bc  # noqa: E402

DIMENSIONS = {
    "comprehensiveness": "How much detail does the answer provide to cover all "
                         "aspects and details of the question?",
    "diversity": "How varied and rich is the answer in providing different "
                 "perspectives and insights on the question?",
}

JUDGE_SYSTEM = (
    "You are a strict evaluator comparing two answers to the same question on a "
    "single quality dimension. Decide which answer is better on that dimension "
    "ONLY — ignore length padding, style, and which one you think is factually "
    "correct unless the dimension is about it.\n"
    "Output STRICT JSON only: {\"winner\": \"1\"|\"2\"|\"tie\", \"reason\": \"one short sentence\"}"
)


def _safe_str(x) -> str:
    if x is None or (isinstance(x, float) and x != x):
        return ""
    return str(x)


def _flip(case_id: str, arm: str, mode: str, dim: str) -> bool:
    """Deterministic A/B position flip to cancel position bias."""
    h = hashlib.sha1(f"{case_id}|{arm}|{mode}|{dim}".encode()).hexdigest()
    return int(h[:8], 16) % 2 == 1


def _parse(text: str) -> str:
    t = _safe_str(text).strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-z]*\n?|\n?```$", "", t).strip()
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if not m:
        return "tie"
    try:
        w = str(json.loads(m.group(0)).get("winner", "tie")).strip().lower()
    except Exception:
        return "tie"
    return w if w in {"1", "2", "tie"} else "tie"


def compare(llm, question: str, arm_answer: str, vector_answer: str, dim: str,
            flip: bool) -> str:
    """Return 'arm' | 'vector' | 'tie' for which answer wins on `dim`."""
    a, b = (vector_answer, arm_answer) if flip else (arm_answer, vector_answer)
    # answer-1 is arm unless flipped
    user = (f"QUESTION:\n{_safe_str(question)}\n\n"
            f"DIMENSION — {dim}: {DIMENSIONS[dim]}\n\n"
            f"ANSWER 1:\n{_safe_str(a)}\n\n"
            f"ANSWER 2:\n{_safe_str(b)}\n\n"
            f"Which answer is better on {dim}?")
    try:
        resp = llm.complete(system=JUDGE_SYSTEM, user=user, temperature=0.0)
    except TypeError:
        resp = llm.complete(system=JUDGE_SYSTEM, user=user)
    w = _parse(getattr(resp, "text", None) or getattr(resp, "content", None) or str(resp))
    if w == "tie":
        return "tie"
    winner_is_first = (w == "1")
    first_is_arm = not flip
    arm_won = winner_is_first == first_is_arm
    return "arm" if arm_won else "vector"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs-vector", required=True)
    ap.add_argument("--inputs-graph", required=True)
    ap.add_argument("--judge-llm", default="grok/grok-4.3")
    ap.add_argument("--modes", default="graph,vector_graph")
    ap.add_argument("--dimensions", default="comprehensiveness,diversity")
    ap.add_argument("--limit-cases", type=int, default=0)
    ap.add_argument("--out", default=f"outputs/evaluation/graphrag_metrics_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.json")
    args = ap.parse_args()

    bc.bootstrap(verbose=True)
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    dims = [d.strip() for d in args.dimensions.split(",") if d.strip() in DIMENSIONS]

    # vector answers keyed by case_id
    vec = {}
    for f in glob.glob(args.inputs_vector):
        r = json.load(open(f))
        vec[r["case_id"]] = r
    # graph/hybrid answers keyed by (case_id, arm, mode)
    arms_ans = {}
    for f in glob.glob(args.inputs_graph):
        r = json.load(open(f))
        m = r.get("retrieval") or r.get("mode")
        if m in modes:
            arms_ans[(r["case_id"], r["arm"], m)] = r
    cases = sorted(vec)
    if args.limit_cases:
        cases = cases[: args.limit_cases]
    print(f"== GraphRAG metrics: {len(cases)} cases, modes={modes}, dims={dims}, judge={args.judge_llm} ==")

    from seocho.store.llm import create_llm_backend
    provider, model = args.judge_llm.split("/", 1)
    llm = create_llm_backend(provider=provider.strip(), model=model.strip())

    # win/tie/loss per (arm, mode, dim)
    agg = defaultdict(lambda: {"arm": 0, "vector": 0, "tie": 0})
    records = []
    n = 0
    t0 = time.perf_counter()
    for cid in cases:
        vr = vec[cid]
        q = vr.get("query", "")
        v_ans = vr.get("answer", "")
        for (ckey, arm, mode), ar in list(arms_ans.items()):
            if ckey != cid:
                continue
            for dim in dims:
                w = compare(llm, q, ar.get("answer", ""), v_ans, dim,
                            _flip(cid, arm, mode, dim))
                agg[(arm, mode, dim)][w] += 1
                records.append({"case_id": cid, "slice": vr.get("slice"), "arm": arm,
                                "mode": mode, "dimension": dim, "winner": w})
                n += 1
        if n and n % 40 == 0:
            print(f"  {n} comparisons ({round(time.perf_counter()-t0)}s)", flush=True)

    summary = {}
    for (arm, mode, dim), c in sorted(agg.items()):
        decided = c["arm"] + c["vector"]
        win_rate = round(c["arm"] / decided, 3) if decided else None
        summary[f"{arm}|{mode}|{dim}"] = {
            "arm": arm, "mode": mode, "dimension": dim,
            "arm_wins": c["arm"], "vector_wins": c["vector"], "ties": c["tie"],
            "arm_win_rate_vs_vector": win_rate,
        }

    out_path = ROOT / args.out
    bc.atomic_write_json(out_path, {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "judge_llm": args.judge_llm, "metric": "graphrag_pairwise_vs_vector",
        "dimensions": dims, "modes": modes, "n_comparisons": n,
        "summary": summary, "records": records,
    })
    print(f"\n== wrote {out_path.relative_to(ROOT)} ==")
    print(f"\n(arm vs vector win-rate; >0.5 = arm's answers richer than vector)")
    print(f"{'arm':<14}{'mode':<14}{'dimension':<18} win/tie/loss  win_rate")
    print("-" * 74)
    for row in summary.values():
        print(f"{row['arm']:<14}{row['mode']:<14}{row['dimension']:<18} "
              f"{row['arm_wins']}/{row['ties']}/{row['vector_wins']}        {row['arm_win_rate_vs_vector']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
