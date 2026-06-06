#!/usr/bin/env python3
"""Compare the MiniMax-M2.7 strong-reference verdicts to the fast-rubric panel.

§20 DISCLOSURE (printed): the M2.7 reference is NOT a human anchor — it cannot
escape LLM-judging-LLM circularity, and M2.7 is itself a panel judge so the
reference↔M2.7 column is partly mechanical. Read reference↔gpt-oss / ↔DeepSeek /
↔PANEL as the informative cross-checks (do the fast judges match a careful
reasoning judge?), NOT as truth.

Run (after label_reference_m27.py): python examples/contextgraph/analyze_reference.py
"""
from __future__ import annotations
import json
from pathlib import Path
from collections import defaultdict, Counter

ROOT = Path(__file__).resolve().parents[2]
CAL = ROOT / "outputs/evaluation/contextgraph/calibration"
_V = {"correct", "partial", "incorrect"}
_SC = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}


def kappa(a, b):
    if not a:
        return float("nan")
    n = len(a)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum((ca.get(c, 0) / n) * (cb.get(c, 0) / n) for c in _V)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def main():
    ref = json.loads((CAL / "reference_m27.json").read_text())
    ref = {c: v["verdict"] for c, v in ref.items() if v.get("verdict") in _V}
    pj = json.loads((CAL / "calibration_panel_judged.json").read_text())
    key = json.loads((CAL / "_key.json").read_text())
    judged = {r.get("cal_id") or str(r.get("_id", "")).split("|")[0]: r for r in pj["results"]}
    models = pj.get("judge_models", [])
    ids = [c for c in ref if c in judged]

    print("=" * 72)
    print("M2.7 REFERENCE vs fast-rubric panel — NOT human ground truth (§20).")
    print("ref↔M2.7 is partly mechanical (same model). Informative: ref vs gpt-oss/")
    print("DeepSeek/PANEL. This catches careful-vs-fast divergence, not truth.")
    print("=" * 72)
    print(f"\nn = {len(ids)} cases\n")

    R = [ref[c] for c in ids]
    print("reference ↔ judge agreement (Cohen's kappa; raw agree):")
    for m in models:
        J = [judged[c].get("judge_per_model", {}).get(m, {}).get("verdict", "incorrect") for c in ids]
        agree = sum(1 for x, y in zip(R, J) if x == y) / len(ids)
        tag = "  (same-model, partly mechanical)" if "M2.7" in m else ""
        print(f"   ref vs {m:<26} kappa={kappa(R, J):+.2f}  agree={agree:.0%}{tag}")
    P = [judged[c].get("panel_verdict") or judged[c].get("judge_verdict", "incorrect") for c in ids]
    agree = sum(1 for x, y in zip(R, P) if x == y) / len(ids)
    print(f"   ref vs {'PANEL (majority)':<26} kappa={kappa(R, P):+.2f}  agree={agree:.0%}")

    print("\nreference verdict distribution vs panel:")
    print(f"   reference : {dict(Counter(R))}")
    print(f"   panel     : {dict(Counter(P))}")

    print("\nbias by slice/lane  (panel_score − reference_score; + = panel over-credits vs careful ref):")
    by = defaultdict(list)
    for i, c in enumerate(ids):
        k = key[c]
        by[(k["slice"], k["lane"])].append(_SC[P[i]] - _SC[R[i]])
    for cell in sorted(by):
        v = by[cell]
        print(f"   {cell[0]:<16} {cell[1]:<10} Δ={sum(v)/len(v):+.2f}  (n={len(v)})")

    print("\nreference ≠ panel — cases to inspect:")
    for i, c in enumerate(ids):
        if R[i] != P[i]:
            k = key[c]
            pm = {m.split('/')[-1][:8]: judged[c].get("judge_per_model", {}).get(m, {}).get("verdict", "?")[:4] for m in models}
            print(f"   {c} [{k['slice']}/{k['lane']}] ref={R[i]:<9} panel={P[i]:<9} {pm}")


if __name__ == "__main__":
    main()
