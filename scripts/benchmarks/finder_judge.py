#!/usr/bin/env python3
"""Offline scoring pass for the vector-vs-graph experiment.

Reads saved per-answer partial JSONs (from finder_vector_arm.py and
finder_4arm_sample.py), and augments each with:
  - token_f1   : deterministic SQuAD-style token F1 vs the gold answer
  - judge_*    : LLM-as-judge (grok-4.3) verdict/score vs the gold answer

Generator and judge are both grok-4.3. Self-preference bias is therefore
present but UNIFORM across all lanes (vector/graph/hybrid are all grok-
generated), so the relative comparison stays fair; absolute judge scores may be
lenient. Disclosed per CLAUDE.md §20.3. Judge is deterministic (temperature 0,
fixed prompt) for reproducibility (§20.7).

Usage:
  python scripts/benchmarks/finder_judge.py \
      --inputs "outputs/evaluation/finder_vector_arm/<run>/partial/*.json" \
               "outputs/evaluation/finder_4arm_sample/<run>/partial/*.json" \
      --out outputs/evaluation/judged_<tag>.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for path in (SRC, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from examples.finder.lib import bench_common as bc  # noqa: E402

JUDGE_MODEL = "grok/grok-4.3"
JUDGE_PROMPT_ID = "finder_judge@v1"

JUDGE_SYSTEM = (
    "You are a strict evaluator for financial question answering. You receive a "
    "QUESTION, a GOLD answer (ground truth), and a CANDIDATE answer from a "
    "system. Judge ONLY the factual correctness of CANDIDATE relative to GOLD — "
    "ignore writing style, verbosity, and formatting.\n\n"
    "Rules:\n"
    "- GOLD is the ground truth; judge CANDIDATE against it.\n"
    "- Weigh: (1) the final answer/conclusion, (2) the key financial figures "
    "with units and period, (3) the direction/trend (increase/decrease) when the "
    "question asks for it.\n"
    "- Numbers match if equal after removing thousand separators and within "
    "normal rounding (54.4% ~= 54%). Wrong scale (thousands vs millions) or wrong "
    "sign = mismatch.\n"
    "- A CANDIDATE that says 'no data'/'not in context'/refuses, or that "
    "fabricates figures not in GOLD, is INCORRECT.\n"
    "- Do NOT credit coincidental numbers (e.g., years) when the actual answer is "
    "wrong.\n"
    "- Strict partial credit: only when the core figures are right but the final "
    "answer is incomplete or a secondary part is wrong.\n\n"
    "Output STRICT JSON only, no markdown:\n"
    '{"verdict":"correct|partial|incorrect","score":1.0,'
    '"matched":["..."],"missing_or_wrong":["..."],"rationale":"1-2 sentences"}'
)

_SCORE = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}


def _safe_str(x) -> str:
    if x is None:
        return ""
    if isinstance(x, float) and x != x:
        return ""
    return str(x)


def token_f1(pred, gold) -> float:
    def norm(s):
        return re.sub(r"[^a-z0-9 ]", " ", _safe_str(s).lower()).split()

    p, g = norm(pred), norm(gold)
    if not p or not g:
        return 0.0
    common = Counter(p) & Counter(g)
    ns = sum(common.values())
    if ns == 0:
        return 0.0
    prec, rec = ns / len(p), ns / len(g)
    return round(2 * prec * rec / (prec + rec), 4)


def _parse_judge(text: str) -> dict:
    t = _safe_str(text).strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-z]*\n?|\n?```$", "", t).strip()
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if not m:
        return {
            "verdict": "incorrect",
            "score": 0.0,
            "rationale": "unparseable judge output",
            "matched": [],
            "missing_or_wrong": [],
            "parse_error": True,
        }
    try:
        d = json.loads(m.group(0))
    except Exception:
        return {
            "verdict": "incorrect",
            "score": 0.0,
            "rationale": "json error",
            "matched": [],
            "missing_or_wrong": [],
            "parse_error": True,
        }
    verdict = str(d.get("verdict", "incorrect")).lower().strip()
    score = d.get("score")
    if not isinstance(score, (int, float)):
        score = _SCORE.get(verdict, 0.0)
    return {
        "verdict": verdict if verdict in _SCORE else "incorrect",
        "score": float(score),
        "rationale": str(d.get("rationale", ""))[:300],
        "matched": d.get("matched", []),
        "missing_or_wrong": d.get("missing_or_wrong", []),
        "parse_error": False,
    }


def judge_one(llm, query: str, gold: str, candidate: str) -> dict:
    user = (
        f"QUESTION:\n{_safe_str(query)}\n\n"
        f"GOLD ANSWER (ground truth):\n{_safe_str(gold)}\n\n"
        f"CANDIDATE ANSWER:\n{_safe_str(candidate)}"
    )
    try:
        resp = llm.complete(system=JUDGE_SYSTEM, user=user, temperature=0.0)
    except TypeError:
        resp = llm.complete(system=JUDGE_SYSTEM, user=user)
    txt = getattr(resp, "text", None) or getattr(resp, "content", None) or str(resp)
    return _parse_judge(txt)


def lane_key(r: dict) -> tuple:
    """(slice, retrieval, arm) — vector lane has arm n-a."""
    retrieval = r.get("retrieval") or r.get("mode") or "graph"
    arm = r.get("arm", "?")
    if retrieval == "vector":
        arm = "n-a"
    return (r.get("slice", "?"), retrieval, arm)


def _panel(per_judge: dict) -> dict:
    """Aggregate {model: {verdict,score}} into a panel verdict/score.

    panel_score = mean of judge scores; panel_verdict = majority verdict
    (ties broken toward the lower/stricter verdict); disagreement = judges did
    not all agree.
    """
    scores = [v["score"] for v in per_judge.values()]
    verdicts = [v["verdict"] for v in per_judge.values()]
    panel_score = round(sum(scores) / len(scores), 4) if scores else 0.0
    counts = Counter(verdicts)
    top = max(counts.values())
    winners = [
        vd for vd in ("incorrect", "partial", "correct") if counts.get(vd, 0) == top
    ]
    panel_verdict = winners[
        0
    ]  # stricter wins ties (incorrect < partial < correct order)
    return {
        "panel_score": panel_score,
        "panel_verdict": panel_verdict,
        "disagreement": len(set(verdicts)) > 1,
    }


def _cohen_kappa(labels_a: list, labels_b: list) -> float:
    """Cohen's kappa for two raters over the same items (categorical labels)."""
    n = len(labels_a)
    if n == 0:
        return 0.0
    cats = set(labels_a) | set(labels_b)
    po = sum(1 for a, b in zip(labels_a, labels_b) if a == b) / n
    ca, cb = Counter(labels_a), Counter(labels_b)
    pe = sum((ca.get(c, 0) / n) * (cb.get(c, 0) / n) for c in cats)
    if pe >= 1.0:
        return 1.0
    return round((po - pe) / (1 - pe), 4)


def _inter_judge_agreement(judged: list, judge_models: list) -> dict:
    """Pairwise agreement rate + Cohen's kappa across judge models."""
    out = {}
    for i in range(len(judge_models)):
        for j in range(i + 1, len(judge_models)):
            ma, mb = judge_models[i], judge_models[j]
            la, lb = [], []
            for r in judged:
                pj = r.get("judge_per_model", {})
                if ma in pj and mb in pj:
                    la.append(pj[ma]["verdict"])
                    lb.append(pj[mb]["verdict"])
            if la:
                agree = sum(1 for a, b in zip(la, lb) if a == b) / len(la)
                out[f"{ma} vs {mb}"] = {
                    "n": len(la),
                    "agreement": round(agree, 3),
                    "cohen_kappa": _cohen_kappa(la, lb),
                }
    return out


def _wilcoxon(deltas: list) -> dict:
    """Wilcoxon signed-rank p-value for paired deltas (scipy if available)."""
    nz = [d for d in deltas if d != 0]
    if len(nz) < 1:
        return {"n_nonzero": 0, "p_value": None, "method": "none"}
    try:
        from scipy.stats import wilcoxon  # type: ignore

        stat, p = wilcoxon(nz)
        return {"n_nonzero": len(nz), "p_value": round(float(p), 5), "method": "scipy"}
    except Exception:
        return {"n_nonzero": len(nz), "p_value": None, "method": "unavailable"}


def _paired_analysis(judged: list) -> dict:
    """Same-case paired comparison: vector vs each graph/hybrid (retrieval,arm) lane.

    For every case_id present in both the vector lane and a graph/hybrid lane,
    compute panel_score deltas → win/tie/loss counts + Wilcoxon. This is the
    statistically honest comparison (paired, same case) vs lane means.
    """
    # index panel scores: case_id -> lane(retrieval|arm) -> score
    by_case: dict = defaultdict(dict)
    for r in judged:
        ret = r.get("retrieval") or r.get("mode") or "graph"
        arm = "n-a" if ret == "vector" else r.get("arm", "?")
        by_case[r["case_id"]][f"{ret}|{arm}"] = r.get(
            "panel_score", r.get("judge_score", 0.0)
        )
    # vector is the baseline lane; compare every OTHER lane (graph + vector_graph)
    # against it. NB: exclude only the exact baseline "vector|n-a" — not anything
    # starting with "vector" (that wrongly dropped the vector_graph hybrid lanes).
    pairs = {}
    lanes = sorted(
        {lane for c in by_case.values() for lane in c if lane != "vector|n-a"}
    )
    for lane in lanes:
        deltas, win = [], {"lane_wins": 0, "tie": 0, "vector_wins": 0}
        for case, scores in by_case.items():
            if "vector|n-a" in scores and lane in scores:
                d = scores[lane] - scores["vector|n-a"]
                deltas.append(d)
                if d > 0:
                    win["lane_wins"] += 1
                elif d < 0:
                    win["vector_wins"] += 1
                else:
                    win["tie"] += 1
        if deltas:
            pairs[f"{lane} vs vector"] = {
                "n_paired": len(deltas),
                "mean_delta": round(sum(deltas) / len(deltas), 4),
                **win,
                "wilcoxon": _wilcoxon(deltas),
            }
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--inputs", nargs="+", required=True, help="Glob(s) of partial result JSONs."
    )
    ap.add_argument(
        "--out",
        default=f"outputs/evaluation/judged_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.json",
    )
    ap.add_argument(
        "--judge-llms",
        default=JUDGE_MODEL,
        help="Comma list of judges, e.g. grok/grok-4.3,openai/gpt-5.5. "
        "Multiple judges form a cross-vendor panel (removes self-preference).",
    )
    ap.add_argument(
        "--judge-llm", default=None, help="(deprecated alias for --judge-llms)"
    )
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    bc.bootstrap(verbose=True)

    judge_models = [
        m.strip() for m in (args.judge_llm or args.judge_llms).split(",") if m.strip()
    ]

    files: list[str] = []
    for pat in args.inputs:
        files.extend(sorted(glob.glob(pat)))
    files = sorted(set(files))
    if args.limit:
        files = files[: args.limit]
    print(f"== judging {len(files)} answers with panel {judge_models} ==")

    from seocho.store.llm import create_llm_backend

    judges = {}
    for spec in judge_models:
        provider, model = spec.split("/", 1)
        judges[spec] = create_llm_backend(
            provider=provider.strip(), model=model.strip()
        )

    judged: list[dict] = []
    t0 = time.perf_counter()
    for i, f in enumerate(files, 1):
        try:
            r = json.load(open(f))
        except Exception:
            continue
        gold, cand, q = r.get("expected_answer"), r.get("answer"), r.get("query", "")
        r["token_f1"] = token_f1(cand, gold)
        per_model = {}
        for spec, llm in judges.items():
            jr = judge_one(llm, q, gold, cand)
            per_model[spec] = {
                "verdict": jr["verdict"],
                "score": jr["score"],
                "rationale": jr["rationale"],
            }
        r["judge_per_model"] = per_model
        r["judge_models"] = judge_models
        panel = _panel(per_model)
        r["panel_score"] = panel["panel_score"]
        r["panel_verdict"] = panel["panel_verdict"]
        r["judge_disagreement"] = panel["disagreement"]
        # Back-compat single-judge fields = panel.
        r["judge_score"] = panel["panel_score"]
        r["judge_verdict"] = panel["panel_verdict"]
        judged.append(r)
        if i % 10 == 0 or i == len(files):
            print(
                f"  [{i}/{len(files)}] judged ({round(time.perf_counter()-t0)}s)",
                flush=True,
            )

    # Aggregate by (slice, retrieval, arm) on the PANEL score.
    by = defaultdict(list)
    for r in judged:
        by[lane_key(r)].append(r)
    summary = {}
    for k, runs in sorted(by.items()):
        slc, ret, arm = k
        summary[f"{slc}|{ret}|{arm}"] = {
            "slice": slc,
            "retrieval": ret,
            "arm": arm,
            "n": len(runs),
            "judge_score_mean": round(
                sum(x["panel_score"] for x in runs) / len(runs), 3
            ),
            "token_f1_mean": round(sum(x["token_f1"] for x in runs) / len(runs), 3),
            "overlap_mean": round(
                sum(x["evaluation"]["number_overlap_ratio"] for x in runs) / len(runs),
                3,
            ),
            "correct": sum(1 for x in runs if x["panel_verdict"] == "correct"),
            "partial": sum(1 for x in runs if x["panel_verdict"] == "partial"),
            "incorrect": sum(1 for x in runs if x["panel_verdict"] == "incorrect"),
        }

    agreement = (
        _inter_judge_agreement(judged, judge_models) if len(judge_models) > 1 else {}
    )
    paired = _paired_analysis(judged)

    out_path = ROOT / args.out
    bc.atomic_write_json(
        out_path,
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "judge_models": judge_models,
            "judge_prompt_id": JUDGE_PROMPT_ID,
            "n_judged": len(judged),
            "summary": summary,
            "inter_judge_agreement": agreement,
            "paired_vs_vector": paired,
            "results": judged,
        },
    )
    print(f"\n== wrote {out_path.relative_to(ROOT)} ==")
    print(
        f"\n{'slice':<22} {'retrieval':<13} {'arm':<13} n  judge  tok_f1  overlap  (C/P/I)"
    )
    print("-" * 92)
    for row in summary.values():
        print(
            f"{row['slice']:<22} {row['retrieval']:<13} {row['arm']:<13} {row['n']:<2} "
            f"{row['judge_score_mean']:.3f}  {row['token_f1_mean']:.3f}   {row['overlap_mean']:.3f}    "
            f"({row['correct']}/{row['partial']}/{row['incorrect']})"
        )
    if agreement:
        print("\n-- inter-judge agreement --")
        for k, v in agreement.items():
            print(
                f"  {k}: agree={v['agreement']} kappa={v['cohen_kappa']} (n={v['n']})"
            )
    if paired:
        print("\n-- paired vs vector (same-case panel-score deltas) --")
        for k, v in paired.items():
            wp = v["wilcoxon"]["p_value"]
            print(
                f"  {k}: n={v['n_paired']} mean_delta={v['mean_delta']:+.3f} "
                f"(lane/tie/vec = {v['lane_wins']}/{v['tie']}/{v['vector_wins']}) "
                f"wilcoxon_p={wp}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
