#!/usr/bin/env python3
"""Strong-MODEL reference labeler for arbiter calibration — MiniMax-M2.7 reasoning.

NOT a human ground-truth anchor (§20 disclosure, printed at runtime): this is a
stronger/reasoning model used as a careful REFERENCE judge. It CANNOT escape
LLM-judging-LLM circularity — if M2.7 shares the panel's bias it will not detect
it. It catches "fast-rubric vs careful-reasoning" divergence, not divergence from
reality. Moreover M2.7 is already one of the 3 panel judges, so reference↔M2.7
agreement is partly mechanical; the informative comparisons are reference vs
gpt-oss / DeepSeek (different families) and reference vs PANEL.

What makes this a DIFFERENT judgment process than the panel's fast rubric: a
rigorous deliberative prompt that (1) restates what a correct answer requires,
(2) decomposes the GOLD into atomic required claims, (3) checks each claim against
the candidate, (4) penalizes hallucinated/contradicted extra claims, (5) maps the
coverage to correct/partial/incorrect with the rule stated explicitly. Reasoning
stays in the model's reasoning channel; returned content is strict JSON.

Run: python examples/contextgraph/label_reference_m27.py
"""
from __future__ import annotations
import json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ[k] = v
from seocho.store.llm import create_llm_backend

CAL = ROOT / "outputs/evaluation/contextgraph/calibration"
OUT = CAL / "reference_m27.json"

REF_SYSTEM = (
    "You are a meticulous evaluation referee for a decision-making email-QA task. "
    "You judge ONLY factual correctness of the CANDIDATE answer relative to the GOLD "
    "answer — ignore writing style, verbosity, ordering, and formatting. Be rigorous "
    "and impartial; do not be lenient and do not be harsh, be ACCURATE.\n\n"
    "Reason step by step (privately), then output your verdict. Follow this procedure:\n"
    "1. State what a correct answer to the QUESTION must contain.\n"
    "2. Decompose the GOLD answer into its atomic required claims (each: an actor, "
    "an action/proposal/position/decision, and any key qualifier such as date, "
    "target, or direction for/against).\n"
    "3. For EACH gold claim, mark it: PRESENT (candidate states it correctly), "
    "PARTIAL (candidate gestures at it but is vague/imprecise/incomplete), or "
    "MISSING/WRONG (absent or contradicted).\n"
    "4. Check the candidate's EXTRA claims not in gold: if any are fabricated or "
    "contradicted by the question/gold, that is a correctness penalty.\n"
    "5. Map to a verdict with this rule:\n"
    "   - correct  = ALL core gold claims PRESENT and accurate, no fabricated claims.\n"
    "   - partial  = the core actors/actions are right but a key qualifier is "
    "wrong/missing, OR only some of several required claims are covered.\n"
    "   - incorrect = wrong actors/action/decision, 'no data'/refusal/'not in "
    "context', or fabrication of the core claim.\n\n"
    "Output STRICT JSON only (no markdown, no prose outside the JSON):\n"
    '{"verdict":"correct|partial|incorrect","score":1.0,'
    '"gold_claims":[{"claim":"...","status":"present|partial|missing"}],'
    '"fabrications":["..."],"rationale":"one or two sentences"}'
)

_SCORE = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}


def _parse(text):
    m = re.search(r"\{.*\}", str(text), re.S)
    if not m:
        return {"verdict": "incorrect", "score": 0.0, "rationale": "unparseable", "raw": str(text)[:200]}
    try:
        d = json.loads(m.group(0))
    except Exception:
        return {"verdict": "incorrect", "score": 0.0, "rationale": "json error", "raw": str(text)[:200]}
    v = str(d.get("verdict", "incorrect")).lower().strip()
    if v not in _SCORE:
        v = "incorrect"
    d["verdict"] = v
    d["score"] = _SCORE[v]
    return d


def main():
    print("=" * 72)
    print("DISCLOSURE: MiniMax-M2.7 REFERENCE labels — NOT a human ground-truth anchor.")
    print("Cannot escape LLM-judging-LLM circularity; M2.7 is also a panel judge so")
    print("reference↔M2.7 is partly mechanical. Informative: ref vs gpt-oss/DeepSeek/panel.")
    print("=" * 72)
    parts = sorted((CAL / "partial").glob("*.json"))
    llm = create_llm_backend(provider="mara", model="MiniMax-M2.7")
    out = {}
    # resume-safe
    if OUT.exists():
        out = json.loads(OUT.read_text())
    for f in parts:
        d = json.loads(f.read_text())
        cid = d.get("cal_id") or f.stem
        if cid in out:
            continue
        user = (f"QUESTION:\n{d.get('query','')}\n\nGOLD ANSWER:\n{d.get('expected_answer','')}\n\n"
                f"CANDIDATE ANSWER:\n{d.get('answer','')}\n\nJudge per the procedure. Output the JSON.")
        try:
            resp = llm.complete(system=REF_SYSTEM, user=user, temperature=0.0)
            parsed = _parse(getattr(resp, "text", resp))
        except Exception as e:
            parsed = {"verdict": None, "score": None, "rationale": f"ERROR {type(e).__name__}: {str(e)[:80]}"}
        out[cid] = parsed
        OUT.write_text(json.dumps(out, indent=2))  # incremental persist (§ cost)
        print(f"  {cid}: {parsed.get('verdict')}  ({str(parsed.get('rationale',''))[:70]})")
    n_ok = sum(1 for v in out.values() if v.get("verdict"))
    print(f"\nlabeled {n_ok}/{len(parts)} -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
