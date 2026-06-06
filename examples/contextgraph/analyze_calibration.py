#!/usr/bin/env python3
"""Arbiter calibration analysis — human vs LLM-judge agreement ($0, no LLM).

Joins the human-labeled calibration CSV with the 3-model panel verdicts and asks
the questions that decide whether the arbiter can be trusted as ground truth:
  1. inter-judge agreement (do the 3 judges agree with EACH OTHER? — the noise we
     ignored when running single-judge).
  2. human↔judge agreement (Cohen's kappa: human vs each model, human vs panel) —
     is the judge a faithful proxy for a human?
  3. systematic bias by slice/lane (does the judge over/under-credit graph's
     grounded-quote answers vs vector's prose? where does it diverge from human?).
  4. the disagreement list (the cases to read — where human != panel).

Cohen's kappa on a 3-level ordinal scale (correct/partial/incorrect). Reported
WITH the raw agreement rate and n (kappa is unstable at small n — §20.6).

Run (after the user fills your_verdict in human_calibration_BLIND.csv):
  python examples/contextgraph/analyze_calibration.py
"""
from __future__ import annotations
import csv, json
from pathlib import Path
from collections import defaultdict, Counter

ROOT = Path(__file__).resolve().parents[2]
CAL = ROOT / "outputs/evaluation/contextgraph/calibration"
HUMAN = CAL / "human_calibration_BLIND.csv"
PANEL = CAL / "calibration_panel_judged.json"
KEY = CAL / "_key.json"
_V = {"correct", "partial", "incorrect"}


def kappa(a, b):
    """Cohen's kappa for paired label lists a,b over the 3-level scale."""
    if not a:
        return float("nan")
    n = len(a)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum((ca.get(c, 0) / n) * (cb.get(c, 0) / n) for c in _V)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def main():
    key = json.loads(KEY.read_text())
    # human labels
    human = {}
    for r in csv.DictReader(open(HUMAN)):
        v = (r.get("your_verdict (correct|partial|incorrect)") or "").strip().lower()
        if v in _V:
            human[r["cal_id"]] = v
    if not human:
        print("No human labels found yet. Fill 'your_verdict' in:", HUMAN.relative_to(ROOT))
        return
    pj = json.loads(PANEL.read_text())
    judged = {r.get("cal_id") or str(r.get("_id", "")).split("|")[0]: r for r in pj["results"]}
    models = pj.get("judge_models", [])

    # align on cal_ids the human labeled AND we judged
    ids = [c for c in human if c in judged]
    print(f"== Arbiter calibration ({len(ids)} cases labeled by human + panel-judged) ==\n")

    # 1. inter-judge (from the judged file if present, else recompute pairwise)
    print("1. inter-judge agreement (judges vs each other):")
    ija = pj.get("inter_judge_agreement", {})
    if ija:
        for pair, v in ija.items():
            print(f"   {pair}: agree={v['agreement']:.2f} kappa={v['cohen_kappa']:.2f} (n={v['n']})")
    else:
        print("   (single-judge or not computed)")

    # 2. human vs each judge + panel
    print("\n2. human ↔ judge agreement (Cohen's kappa; raw agree; n):")
    H = [human[c] for c in ids]
    for m in models:
        J = [judged[c].get("judge_per_model", {}).get(m, {}).get("verdict", "incorrect") for c in ids]
        agree = sum(1 for x, y in zip(H, J) if x == y) / len(ids)
        print(f"   human vs {m:<26} kappa={kappa(H, J):+.2f}  agree={agree:.0%}")
    P = [judged[c].get("panel_verdict") or judged[c].get("judge_verdict", "incorrect") for c in ids]
    agree = sum(1 for x, y in zip(H, P) if x == y) / len(ids)
    print(f"   human vs {'PANEL (majority)':<26} kappa={kappa(H, P):+.2f}  agree={agree:.0%}")

    # 3. bias by slice/lane: mean signed verdict gap (judge - human), score scale
    sc = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}
    print("\n3. systematic bias  (panel_score − human_score; + = judge over-credits):")
    by = defaultdict(list)
    for c in ids:
        k = key[c]
        by[(k["slice"], k["lane"])].append(sc[P[ids.index(c)]] - sc[human[c]])
    for cell in sorted(by):
        v = by[cell]
        print(f"   {cell[0]:<16} {cell[1]:<10} Δ={sum(v)/len(v):+.2f}  (n={len(v)})")

    # 4. disagreements to read
    print("\n4. human ≠ panel — cases to inspect:")
    for c in ids:
        if human[c] != P[ids.index(c)]:
            k = key[c]
            pm = {m: judged[c].get("judge_per_model", {}).get(m, {}).get("verdict", "?")[:4] for m in models}
            print(f"   {c} [{k['slice']}/{k['lane']}] human={human[c]:<9} panel={P[ids.index(c)]:<9} per-judge={pm}")


if __name__ == "__main__":
    main()
