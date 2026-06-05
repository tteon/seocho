#!/usr/bin/env python3
"""Offline scoring pass for the vector-vs-graph experiment.

Reads saved per-answer partial JSONs (from finder_vector_arm.py and
finder_4arm_sample.py), and augments each with:
  - token_f1   : deterministic SQuAD-style token F1 vs the gold answer
  - judge_*    : LLM-as-judge verdict/score vs the gold answer
  - evidence_use_* : optional typed-evidence-bundle faithfulness judge for qualitative cases

The default judge is MARA DeepSeek-V3.1 so evaluation stays on the team-preferred
gateway. Judge is deterministic (temperature 0, fixed prompt) for
reproducibility (§20.7).

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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples.finder.lib import bench_common as bc  # noqa: E402

JUDGE_MODEL = "mara/DeepSeek-V3.1"
JUDGE_PROMPT_ID = "finder_judge@v1"
EVIDENCE_JUDGE_PROMPT_ID = "seocho_evidence_bundle_judge@v1"

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

DECISION_JUDGE_SYSTEM = (
    "You are a strict evaluator for DECISION-TRACKING over email threads. You "
    "receive a QUESTION, a GOLD answer (derived from human annotations of the "
    "thread), and a CANDIDATE answer from a system. Judge ONLY factual "
    "correctness relative to GOLD — ignore writing style, verbosity, formatting.\n\n"
    "Rules:\n"
    "- GOLD is ground truth. Weigh: (1) the right PROPOSALS/decisions/outcome, "
    "(2) WHO said/proposed/decided/objected (correct participants), (3) the "
    "POSITION direction (support vs oppose) when asked, (4) WHEN (initiator/date) "
    "for factual questions.\n"
    "- Correct = candidate identifies the same decision elements and actors as "
    "GOLD. Partial = core proposal/decision right but a participant or position "
    "is missing/wrong. Incorrect = wrong actors/decision, 'no data', refusal, or "
    "fabricated participants/proposals not in GOLD.\n"
    "- Paraphrase is fine; do not require exact wording. Do NOT credit naming a "
    "participant if their role/position is wrong.\n\n"
    "Output STRICT JSON only, no markdown:\n"
    '{"verdict":"correct|partial|incorrect","score":1.0,'
    '"matched":["..."],"missing_or_wrong":["..."],"rationale":"1-2 sentences"}'
)

_JUDGE_SYSTEMS = {"financial": JUDGE_SYSTEM, "decision": DECISION_JUDGE_SYSTEM}

_SCORE = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}

EVIDENCE_JUDGE_SYSTEM = (
    "You are a strict evaluator for SEOCHO typed evidence bundles. You receive "
    "a QUESTION, a GOLD answer, a CANDIDATE answer, and a compact typed evidence "
    "bundle produced by SEOCHO's evidence swarm. Judge whether the CANDIDATE's "
    "use of evidence is faithful to the typed bundle. This is NOT a replacement "
    "for the normal gold-answer correctness judge.\n\n"
    "Rules:\n"
    "- GOLD is provided for context, but this rubric scores evidence use: whether "
    "the candidate overclaims, underclaims, or faithfully reports insufficiency.\n"
    "- Use the typed bundle to identify whether required slots, relation paths, "
    "provenance, support status, and insufficiency signals justify the answer.\n"
    "- For qualitative/non-numeric answers, do NOT require numeric overlap. "
    "Credit correct qualitative factors, limitations, and abstentions.\n"
    "- A candidate that correctly states insufficiency or absence of quantitative "
    "data may be partial or correct when GOLD also says the references lack "
    "specific figures.\n"
    "- Penalize fabricated facts, unsupported numbers, missing required slots, "
    "or claims contradicted by the insufficiency signals.\n\n"
    "Output STRICT JSON only, no markdown. Use verdict for evidence use:\n"
    '{"verdict":"correct|partial|incorrect","score":1.0,'
    '"evidence_support":"supported|insufficient|contradicted",'
    '"bundle_use":"faithful|underclaim|overclaim",'
    '"matched":["..."],"missing_or_wrong":["..."],"rationale":"1-2 sentences"}'
)


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
        return {"verdict": "incorrect", "score": 0.0, "rationale": "unparseable judge output",
                "matched": [], "missing_or_wrong": [], "parse_error": True}
    try:
        d = json.loads(m.group(0))
    except Exception:
        return {"verdict": "incorrect", "score": 0.0, "rationale": "json error",
                "matched": [], "missing_or_wrong": [], "parse_error": True}
    verdict = str(d.get("verdict", "incorrect")).lower().strip()
    score = d.get("score")
    if not isinstance(score, (int, float)):
        score = _SCORE.get(verdict, 0.0)
    return {"verdict": verdict if verdict in _SCORE else "incorrect",
            "score": float(score), "rationale": str(d.get("rationale", ""))[:300],
            "matched": d.get("matched", []), "missing_or_wrong": d.get("missing_or_wrong", []),
            "parse_error": False}


def judge_one(llm, query: str, gold: str, candidate: str, judge_system: str = JUDGE_SYSTEM) -> dict:
    user = (f"QUESTION:\n{_safe_str(query)}\n\n"
            f"GOLD ANSWER (ground truth):\n{_safe_str(gold)}\n\n"
            f"CANDIDATE ANSWER:\n{_safe_str(candidate)}")
    try:
        resp = llm.complete(system=judge_system, user=user, temperature=0.0)
    except TypeError:
        resp = llm.complete(system=judge_system, user=user)
    txt = getattr(resp, "text", None) or getattr(resp, "content", None) or str(resp)
    return _parse_judge(txt)


def evidence_judge_one(
    llm,
    query: str,
    gold: str,
    candidate: str,
    evidence_bundle: dict,
) -> dict:
    bundle = _compact_evidence_bundle(evidence_bundle)
    user = (
        f"QUESTION:\n{_safe_str(query)}\n\n"
        f"GOLD ANSWER (ground truth):\n{_safe_str(gold)}\n\n"
        f"CANDIDATE ANSWER:\n{_safe_str(candidate)}\n\n"
        "TYPED EVIDENCE BUNDLE JSON:\n"
        f"{json.dumps(bundle, ensure_ascii=True, default=str, indent=2)}"
    )
    try:
        resp = llm.complete(
            system=EVIDENCE_JUDGE_SYSTEM,
            user=user,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
    except TypeError:
        resp = llm.complete(system=EVIDENCE_JUDGE_SYSTEM, user=user)
    txt = getattr(resp, "text", None) or getattr(resp, "content", None) or str(resp)
    parsed = _parse_judge(txt)
    try:
        raw = json.loads(re.search(r"\{.*\}", _safe_str(txt), re.DOTALL).group(0))  # type: ignore[union-attr]
    except Exception:
        raw = {}
    parsed["evidence_support"] = str(raw.get("evidence_support", "") or "").strip().lower()
    parsed["bundle_use"] = str(raw.get("bundle_use", "") or "").strip().lower()
    return parsed


def _compact_evidence_bundle(evidence_bundle: dict) -> dict:
    if not isinstance(evidence_bundle, dict):
        return {}
    swarm = evidence_bundle.get("evidence_swarm") or {}
    if not isinstance(swarm, dict):
        swarm = {}
    support = evidence_bundle.get("support_assessment") or {}
    if not isinstance(support, dict):
        support = {}
    scouts = []
    for scout in swarm.get("scouts") or []:
        if not isinstance(scout, dict):
            continue
        scouts.append({
            "scout_id": scout.get("scout_id"),
            "status": scout.get("status"),
            "findings": scout.get("findings") or [],
            "confidence": scout.get("confidence"),
        })
    return {
        "intent": evidence_bundle.get("intent"),
        "focus_slots": evidence_bundle.get("focus_slots") or [],
        "grounded_slots": evidence_bundle.get("grounded_slots") or [],
        "missing_slots": evidence_bundle.get("missing_slots") or [],
        "selected_triples": evidence_bundle.get("selected_triples") or [],
        "provenance": evidence_bundle.get("provenance") or [],
        "support_assessment": {
            "status": support.get("status"),
            "supported": support.get("supported"),
            "reason": support.get("reason"),
            "coverage": support.get("coverage"),
            "missing_slots": support.get("missing_slots") or [],
        },
        "evidence_swarm": {
            "enabled": swarm.get("enabled"),
            "hardness": swarm.get("hardness"),
            "critical_path": swarm.get("critical_path") or [],
            "recommended_next_step": swarm.get("recommended_next_step"),
            "scouts": scouts,
        },
    }


def needs_evidence_judge(record: dict) -> bool:
    if str(record.get("support_quality_gap") or "") == "no_numeric_gold":
        return True
    if str(record.get("slice") or "").startswith("S4_"):
        return True
    expected = _safe_str(record.get("expected_answer"))
    return bool(expected and not _has_substantive_number(expected))


_SUBSTANTIVE_NUM_RE = re.compile(r"-?\$?\d[\d,]*\.?\d*(?:%| million| billion| thousand)?", re.IGNORECASE)
_ORDERED_LIST_MARKER_RE = re.compile(r"(?m)^\s*\d+\.\s+")


def _has_substantive_number(text: str) -> bool:
    cleaned = _ORDERED_LIST_MARKER_RE.sub("", _safe_str(text))
    return bool(_SUBSTANTIVE_NUM_RE.search(cleaned))


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
    winners = [vd for vd in ("incorrect", "partial", "correct") if counts.get(vd, 0) == top]
    panel_verdict = winners[0]  # stricter wins ties (incorrect < partial < correct order)
    return {"panel_score": panel_score, "panel_verdict": panel_verdict,
            "disagreement": len(set(verdicts)) > 1}


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
                    la.append(pj[ma]["verdict"]); lb.append(pj[mb]["verdict"])
            if la:
                agree = sum(1 for a, b in zip(la, lb) if a == b) / len(la)
                out[f"{ma} vs {mb}"] = {"n": len(la), "agreement": round(agree, 3),
                                        "cohen_kappa": _cohen_kappa(la, lb)}
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
        # Decision partials key on `_id` shaped "<case>|<lane>|<arm>"; strip the
        # lane/arm suffix so the SAME case pairs across lanes. FinDER partials use
        # `case_id` directly.
        case_id = r.get("case_id") or str(r.get("_id", "")).split("|")[0]
        by_case[case_id][f"{ret}|{arm}"] = r.get("panel_score", r.get("judge_score", 0.0))
    # vector is the baseline lane; compare every OTHER lane (graph + vector_graph)
    # against it. NB: exclude only the exact baseline "vector|n-a" — not anything
    # starting with "vector" (that wrongly dropped the vector_graph hybrid lanes).
    pairs = {}
    lanes = sorted({lane for c in by_case.values() for lane in c if lane != "vector|n-a"})
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
                **win, "wilcoxon": _wilcoxon(deltas),
            }
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="+", required=True, help="Glob(s) of partial result JSONs.")
    ap.add_argument("--out", default=f"outputs/evaluation/judged_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.json")
    ap.add_argument("--judge-llms", default=JUDGE_MODEL,
                    help="Comma list of judges, e.g. grok/grok-4.3,openai/gpt-5.5. "
                         "Multiple judges form a cross-vendor panel (removes self-preference).")
    ap.add_argument("--judge-llm", default=None, help="(deprecated alias for --judge-llms)")
    ap.add_argument("--judge-domain", default="financial", choices=sorted(_JUDGE_SYSTEMS),
                    help="Judge rubric: 'financial' (FinDER) or 'decision' (email decision-tracking).")
    ap.add_argument("--evidence-judge", default="auto", choices=("auto", "always", "never"),
                    help="Run typed-evidence-bundle judging for qualitative/no-numeric cases.")
    ap.add_argument("--evidence-judge-llms", default=None,
                    help="Comma list of evidence judges. Defaults to --judge-llms.")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    bc.bootstrap(verbose=True)

    judge_system = _JUDGE_SYSTEMS[args.judge_domain]
    judge_models = [m.strip() for m in (args.judge_llm or args.judge_llms).split(",") if m.strip()]
    evidence_judge_models = [
        m.strip() for m in (args.evidence_judge_llms or ",".join(judge_models)).split(",") if m.strip()
    ]
    print(f"== judge domain: {args.judge_domain} ==")

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
        judges[spec] = create_llm_backend(provider=provider.strip(), model=model.strip())
    evidence_judges = {}
    if args.evidence_judge != "never":
        for spec in evidence_judge_models:
            if spec in judges:
                evidence_judges[spec] = judges[spec]
                continue
            provider, model = spec.split("/", 1)
            evidence_judges[spec] = create_llm_backend(provider=provider.strip(), model=model.strip())

    # Incremental persistence: each judged record is appended to a JSONL sidecar
    # the instant it is scored, so a crash in post-processing (or anywhere) can
    # NEVER discard paid judge calls. A re-run resumes by skipping sources already
    # present in the sidecar (judge LLM calls are not cached — losing them is the
    # expensive failure mode this guards against).
    sidecar = ROOT / (str(args.out) + ".jsonl")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    judged: list[dict] = []
    done_src: set = set()
    if sidecar.exists():
        for line in sidecar.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            judged.append(rec)
            if rec.get("_judge_src"):
                done_src.add(rec["_judge_src"])
        if done_src:
            print(f"== resume: {len(done_src)} already judged in {sidecar.name}, skipping them ==")
    sc = open(sidecar, "a")

    t0 = time.perf_counter()
    for i, f in enumerate(files, 1):
        if f in done_src:
            continue
        try:
            r = json.load(open(f))
        except Exception:
            continue
        gold, cand, q = r.get("expected_answer"), r.get("answer"), r.get("query", "")
        r["token_f1"] = token_f1(cand, gold)
        per_model = {}
        _judge_failed = False
        for spec, llm in judges.items():
            # Resilience (§20.2): a judge LLM error (e.g. MARA InternalServerError/
            # timeout exhausting retries) must NOT crash the whole run NOR be imputed
            # as a wrong answer (that would bias the lane down). Skip this case
            # (don't write the sidecar) so a later resume re-judges it; report N
            # attempted vs scored.
            try:
                jr = judge_one(llm, q, gold, cand, judge_system=judge_system)
            except Exception as e:
                print(f"  [judge-skip] {type(e).__name__} on {Path(f).name} — will retry on resume", flush=True)
                _judge_failed = True
                break
            per_model[spec] = {"verdict": jr["verdict"], "score": jr["score"],
                               "rationale": jr["rationale"]}
        if _judge_failed:
            continue
        r["judge_per_model"] = per_model
        r["judge_models"] = judge_models
        panel = _panel(per_model)
        r["panel_score"] = panel["panel_score"]
        r["panel_verdict"] = panel["panel_verdict"]
        r["judge_disagreement"] = panel["disagreement"]
        # Back-compat single-judge fields = panel.
        r["judge_score"] = panel["panel_score"]
        r["judge_verdict"] = panel["panel_verdict"]
        run_evidence_judge = (
            args.evidence_judge == "always"
            or (args.evidence_judge == "auto" and needs_evidence_judge(r))
        )
        if run_evidence_judge and evidence_judges:
            evidence_per_model = {}
            for spec, llm in evidence_judges.items():
                jr = evidence_judge_one(llm, q, gold, cand, r.get("evidence_bundle") or {})
                evidence_per_model[spec] = {
                    "verdict": jr["verdict"],
                    "score": jr["score"],
                    "evidence_support": jr.get("evidence_support", ""),
                    "bundle_use": jr.get("bundle_use", ""),
                    "rationale": jr["rationale"],
                }
            evidence_panel = _panel(evidence_per_model)
            r["evidence_judge_per_model"] = evidence_per_model
            r["evidence_judge_models"] = list(evidence_judges)
            r["evidence_use_score"] = evidence_panel["panel_score"]
            r["evidence_use_verdict"] = evidence_panel["panel_verdict"]
            r["evidence_judge_disagreement"] = evidence_panel["disagreement"]
        r["_judge_src"] = f
        judged.append(r)
        sc.write(json.dumps(r, default=str) + "\n"); sc.flush()
        if i % 10 == 0 or i == len(files):
            print(f"  [{i}/{len(files)}] judged ({round(time.perf_counter()-t0)}s)", flush=True)
    sc.close()

    # Aggregate by (slice, retrieval, arm) on the PANEL score.
    by = defaultdict(list)
    for r in judged:
        by[lane_key(r)].append(r)
    summary = {}
    for k, runs in sorted(by.items()):
        slc, ret, arm = k
        summary[f"{slc}|{ret}|{arm}"] = {
            "slice": slc, "retrieval": ret, "arm": arm, "n": len(runs),
            "judge_score_mean": round(sum(x["panel_score"] for x in runs) / len(runs), 3),
            "token_f1_mean": round(sum(x["token_f1"] for x in runs) / len(runs), 3),
            "overlap_mean": round(sum(x["evaluation"]["number_overlap_ratio"] for x in runs) / len(runs), 3),
            "correct": sum(1 for x in runs if x["panel_verdict"] == "correct"),
            "partial": sum(1 for x in runs if x["panel_verdict"] == "partial"),
            "incorrect": sum(1 for x in runs if x["panel_verdict"] == "incorrect"),
        }
        evidence_scores = [float(x["evidence_use_score"]) for x in runs if "evidence_use_score" in x]
        if evidence_scores:
            summary[f"{slc}|{ret}|{arm}"]["evidence_use_score_mean"] = round(
                sum(evidence_scores) / len(evidence_scores), 3
            )
            summary[f"{slc}|{ret}|{arm}"]["evidence_judged"] = len(evidence_scores)

    # Post-processing must never discard the paid judgments: a bug here used to
    # crash before the write and lose every judge call. Guard both analyses.
    try:
        agreement = _inter_judge_agreement(judged, judge_models) if len(judge_models) > 1 else {}
    except Exception as e:
        print(f"  [warn] inter-judge agreement failed: {type(e).__name__}: {e}")
        agreement = {}
    try:
        paired = _paired_analysis(judged)
    except Exception as e:
        print(f"  [warn] paired analysis failed: {type(e).__name__}: {e}")
        paired = {}

    out_path = ROOT / args.out
    bc.atomic_write_json(out_path, {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "judge_models": judge_models, "judge_prompt_id": JUDGE_PROMPT_ID,
        "evidence_judge_mode": args.evidence_judge,
        "evidence_judge_models": evidence_judge_models if args.evidence_judge != "never" else [],
        "evidence_judge_prompt_id": EVIDENCE_JUDGE_PROMPT_ID,
        "n_judged": len(judged), "summary": summary,
        "inter_judge_agreement": agreement, "paired_vs_vector": paired,
        "results": judged,
    })
    print(f"\n== wrote {out_path.relative_to(ROOT)} ==")
    print(f"\n{'slice':<22} {'retrieval':<13} {'arm':<13} n  judge  ev_use tok_f1  overlap  (C/P/I)")
    print("-" * 92)
    for row in summary.values():
        ev = row.get("evidence_use_score_mean")
        ev_s = f"{ev:.3f}" if isinstance(ev, (int, float)) else "  -  "
        print(f"{row['slice']:<22} {row['retrieval']:<13} {row['arm']:<13} {row['n']:<2} "
              f"{row['judge_score_mean']:.3f}  {ev_s}  {row['token_f1_mean']:.3f}   {row['overlap_mean']:.3f}    "
              f"({row['correct']}/{row['partial']}/{row['incorrect']})")
    if agreement:
        print("\n-- inter-judge agreement --")
        for k, v in agreement.items():
            print(f"  {k}: agree={v['agreement']} kappa={v['cohen_kappa']} (n={v['n']})")
    if paired:
        print("\n-- paired vs vector (same-case panel-score deltas) --")
        for k, v in paired.items():
            wp = v["wilcoxon"]["p_value"]
            print(f"  {k}: n={v['n_paired']} mean_delta={v['mean_delta']:+.3f} "
                  f"(lane/tie/vec = {v['lane_wins']}/{v['tie']}/{v['vector_wins']}) "
                  f"wilcoxon_p={wp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
