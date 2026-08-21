"""arm×organ A/B on the INDEPENDENT GraphRAG-Bench MEDICAL benchmark.

The erb procedural gold was the wrong instrument (all arms coverage ~0 — the answers
live in document prose, not in the entity graph). GraphRAG-Bench is a recognized,
independent graph-RAG benchmark: its questions are answerable from an entity/relation
graph and each carries a gold answer, so it discriminates the organs instead of
bottoming out at abstain. Complex-Reasoning questions are multi-hop — where the intern
(cross-chunk convergence) and schema/pin (correct traversal) organs are load-bearing.

Runs the 7-arm ablation over a type-balanced question sample against the pre-indexed
medical graph (medicallpg), scoring each answer with a cross-vendor DeepSeek judge
(coverage of the gold answer) plus a deterministic gold-answer-substring check, broken
out by question_type.

Usage: python scripts/agentos/e2e_arm_organ_medical.py [--per-type 7] [--gen mara/gpt-oss-120b]
       [--judge mara/DeepSeek-V3.1] [--database medicallpg] [--workspace med]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))
_QUESTIONS = "/home/hadry/openup/_graphrag_benchmark/Datasets/Questions/medical_questions.json"

# reuse the shared harness helpers (RCU wiring, cross-vendor judge, env load)
_spec = importlib.util.spec_from_file_location(
    "matrix", os.path.join(_ROOT, "scripts", "agentos", "e2e_arm_organ_matrix.py"))
_matrix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_matrix)
wire_rcu, judge, _load_mara = _matrix.wire_rcu, _matrix.judge, _matrix._load_mara

_medspec = importlib.util.spec_from_file_location(
    "medidx", os.path.join(_ROOT, "scripts", "agentos", "index_graphrag_medical.py"))
_medidx = importlib.util.module_from_spec(_medspec)
_medspec.loader.exec_module(_medidx)
medical_ontology = _medidx.medical_ontology

# question types to include, in reporting order (Creative Generation excluded — it is
# open-ended generation, not graph-answerable, so it would only add abstain noise).
_TYPES = ["Fact Retrieval", "Complex Reasoning", "Contextual Summarize"]
_WORD = re.compile(r"[a-z0-9]+")


def _sample(per_type: int):
    qs = json.load(open(_QUESTIONS))
    by = {t: [] for t in _TYPES}
    for q in sorted(qs, key=lambda x: x["id"]):        # deterministic order
        t = q.get("question_type")
        if t in by and len(by[t]) < per_type:
            by[t].append(q)
    out = []
    for t in _TYPES:
        out.extend(by[t])
    return out


def _gold_hit(pred: str, gold: str, question: str) -> bool:
    """Deterministic secondary metric: the answer covers the gold's ANSWER-DISTINCTIVE
    content words. Gold sentences restate the question ("...is the most common type of
    skin cancer"), so question tokens are SUBTRACTED first — otherwise a wrong answer
    that echoes the question ("Melanoma is the most common skin cancer") clears the
    threshold (review blocker: question-token leakage). Requires >=60% of the remaining
    distinctive (>=4-char) gold tokens; if the subtraction leaves nothing, falls back to
    the full-token overlap at a stricter 0.8."""
    p = set(_WORD.findall(pred.lower()))
    q = set(_WORD.findall(question.lower()))
    g_all = [w for w in _WORD.findall(gold.lower()) if len(w) >= 4]
    g = [w for w in g_all if w not in q]
    if g:
        return sum(1 for w in g if w in p) / len(g) >= 0.6
    if not g_all:
        return False
    return sum(1 for w in g_all if w in p) / len(g_all) >= 0.8


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--per-type", type=int, default=7)
    ap.add_argument("--gen", default="mara/gpt-oss-120b")
    ap.add_argument("--judge", default="mara/DeepSeek-V3.1")
    ap.add_argument("--uri", default="bolt://localhost:17687")
    ap.add_argument("--password", default="h0gatepass")
    ap.add_argument("--database", default="medicallpg")
    ap.add_argument("--workspace", default="med")
    args = ap.parse_args()
    _load_mara()

    from seocho import Seocho
    from seocho.query.arm_config import ablation_arms
    from seocho.store.llm import create_llm_backend

    onto = medical_ontology()
    client = Seocho.local(
        onto, llm=args.gen, graph=args.uri, neo4j_user="neo4j",
        neo4j_password=args.password, api_key=os.environ.get("MARA_API_KEY"),
        workspace_id=args.workspace)
    fp = wire_rcu(client, onto, args.workspace)

    jp, _, jm = args.judge.partition("/")
    judge_llm = create_llm_backend(provider=jp, model=jm or None,
                                   api_key=os.environ.get("MARA_API_KEY"))

    questions = _sample(args.per_type)
    print(f"gen={args.gen} judge={args.judge} db={args.database} fp={fp[:8]} "
          f"questions={len(questions)} ({args.per_type}/type)", flush=True)

    # ---- reference frame: FLOOR + CEILING controls (user rule; review blocker) ----
    # FLOOR = closed-book: the same generator answers from parametric memory alone
    # (no graph). If the floor is already high, the public-textbook corpus is
    # memorized and coverage cannot support a graph/organ claim — report it as such.
    # CEILING = gold evidence handed as context: the best this generator+judge pair
    # can do when retrieval is perfect. Every organ effect is read WITHIN this band.
    gp, _, gm = args.gen.partition("/")
    gen_llm = create_llm_backend(provider=gp, model=gm or None,
                                 api_key=os.environ.get("MARA_API_KEY"))
    from seocho.store.llm import complete_with_task_hints

    def _reference_arm(name: str, answer_fn):
        per_q = []
        for q in questions:
            try:
                ans = answer_fn(q)
                v = judge(judge_llm, q["question"], ans, [q["answer"]])
                per_q.append({"qid": q["id"], "qtype": q["question_type"],
                              "coverage": v["coverage"], "unsupported": v["unsupported"],
                              "gold_hit": _gold_hit(str(ans), q["answer"], q["question"]),
                              "answer": str(ans)[:250]})
            except Exception as e:
                per_q.append({"qid": q["id"], "qtype": q.get("question_type"),
                              "error": f"{type(e).__name__}: {str(e)[:120]}"})
        ok = [r for r in per_q if "error" not in r]
        n = len(ok) or 1
        s = {"arm": name, "organs_on": [], "n": len(ok), "errors": len(per_q) - len(ok),
             "mean_coverage": round(sum(r["coverage"] for r in ok) / n, 3),
             "gold_hits": sum(1 for r in ok if r["gold_hit"]),
             "confabulations": sum(1 for r in ok if r["unsupported"]),
             "abstains": 0, "guardrail_rejects": 0, "pinned_schema": 0, "ws_enforced": 0,
             "by_type": {t: {"n": sum(1 for r in ok if r["qtype"] == t),
                             "cov": round(sum(r["coverage"] for r in ok if r["qtype"] == t)
                                          / (sum(1 for r in ok if r["qtype"] == t) or 1), 2),
                             "hit": sum(1 for r in ok if r["qtype"] == t and r["gold_hit"])}
                         for t in _TYPES}}
        print(f"\n### {name:22s} (reference control)", flush=True)
        print(f"    coverage={s['mean_coverage']} gold_hits={s['gold_hits']}/{s['n']} "
              f"confab={s['confabulations']} err={s['errors']}", flush=True)
        return {"summary": s, "per_q": per_q}

    def _floor_answer(q):
        r = complete_with_task_hints(
            gen_llm, system="Answer the question concisely in 1-3 sentences.",
            user=q["question"], temperature=0.0, reasoning_mode=False,
            task_hint="answer_synthesis")
        return getattr(r, "text", None) or str(r)

    def _ceiling_answer(q):
        r = complete_with_task_hints(
            gen_llm,
            system="Answer the question concisely using ONLY the provided evidence.",
            user=f"EVIDENCE:\n{q.get('evidence','')}\n\nQUESTION: {q['question']}",
            temperature=0.0, reasoning_mode=False, task_hint="answer_synthesis")
        return getattr(r, "text", None) or str(r)

    results = []
    results.append(_reference_arm("floor_closed_book", _floor_answer))
    results.append(_reference_arm("ceiling_gold_evidence", _ceiling_answer))
    for arm in ablation_arms():
        client._engine._structured_arm = arm
        per_q = []
        for q in questions:
            try:
                ans = client.ask(q["question"], engine="structured", database=args.database)
                md = client.last_query_metadata
                st = md["structured"]
                v = judge(judge_llm, q["question"], ans, [q["answer"]])
                per_q.append({
                    "qid": q["id"], "qtype": q["question_type"],
                    "answer_source": md["answer_source"], "rows": md["result_count"],
                    "schema_source": st["schema_source"], "ws_enforced": st["workspace_enforced"],
                    "guardrail_rejected": st["guardrail_rejected"], "repairs": st["repair_attempts"],
                    "coverage": v["coverage"], "unsupported": v["unsupported"],
                    "gold_hit": _gold_hit(str(ans), q["answer"], q["question"]),
                    "cypher": st["cypher"][:200], "answer": str(ans)[:250],
                })
            except Exception as e:
                per_q.append({"qid": q["id"], "qtype": q.get("question_type"),
                              "error": f"{type(e).__name__}: {str(e)[:140]}"})
        ok = [r for r in per_q if "error" not in r]
        n = len(ok) or 1

        def _by_type(t):
            sub = [r for r in ok if r["qtype"] == t]
            m = len(sub) or 1
            return {"n": len(sub),
                    "cov": round(sum(r["coverage"] for r in sub) / m, 2),
                    "hit": sum(1 for r in sub if r["gold_hit"])}

        summary = {
            "arm": arm.name, "organs_on": arm.organs_on(), "n": len(ok),
            "errors": len(per_q) - len(ok),
            "mean_coverage": round(sum(r["coverage"] for r in ok) / n, 3),
            "gold_hits": sum(1 for r in ok if r["gold_hit"]),
            "confabulations": sum(1 for r in ok if r["unsupported"]),
            "abstains": sum(1 for r in ok if r["answer_source"] != "structured"),
            "guardrail_rejects": sum(1 for r in ok if r.get("guardrail_rejected")),
            "pinned_schema": sum(1 for r in ok if r.get("schema_source") == "pinned"),
            "ws_enforced": sum(1 for r in ok if r.get("ws_enforced")),
            "by_type": {t: _by_type(t) for t in _TYPES},
        }
        if arm.name == "governed-no-intern":
            # honesty label (review blocker #5): interning is an INDEX-TIME property
            # (cross_source_unique) — a query-time flag cannot ablate it, so this arm
            # is a no-op CONTROL (expected == governed). The real intern ablation is
            # a dual-index run (cross_source_unique ON vs OFF), tracked separately.
            summary["note"] = "index-time no-op control — expected identical to governed; real intern ablation = dual-index follow-up"
        results.append({"summary": summary, "per_q": per_q})
        bt = summary["by_type"]
        print(f"\n### {arm.name:22s} organs={arm.organs_on()}", flush=True)
        print(f"    coverage={summary['mean_coverage']} gold_hits={summary['gold_hits']}/{summary['n']} "
              f"confab={summary['confabulations']} abstain={summary['abstains']} "
              f"gr_rej={summary['guardrail_rejects']} pinned={summary['pinned_schema']}/{summary['n']} "
              f"err={summary['errors']}", flush=True)
        print("    by-type cov: " + "  ".join(
            f"{t.split()[0]}={bt[t]['cov']}(hit{bt[t]['hit']}/{bt[t]['n']})" for t in _TYPES), flush=True)

    out = os.path.join(_ROOT, "outputs", "agentos", "medical_arm_organ_results.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"gen": args.gen, "judge": args.judge, "database": args.database,
                   "benchmark": "GraphRAG-Bench/medical", "fingerprint": fp,
                   "per_type": args.per_type, "arms": results}, fh, indent=2)
    print(f"\n=== wrote {out} ===", flush=True)
    print(f"\n{'arm':22s} {'cov':>5s} {'gold':>6s} {'confab':>6s} {'abst':>5s} {'pinned':>7s} {'ws':>6s}", flush=True)
    for r in results:
        s = r["summary"]
        print(f"{s['arm']:22s} {s['mean_coverage']:5.2f} {str(s['gold_hits'])+'/'+str(s['n']):>6s} "
              f"{s['confabulations']:6d} {s['abstains']:5d} "
              f"{str(s['pinned_schema'])+'/'+str(s['n']):>7s} "
              f"{str(s['ws_enforced'])+'/'+str(s['n']):>6s}", flush=True)


if __name__ == "__main__":
    main()
