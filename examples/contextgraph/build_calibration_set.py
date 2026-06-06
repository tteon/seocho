#!/usr/bin/env python3
"""Build a BLIND human-calibration set for the arbiter (judge-of-the-judge).

The LLM judge is the ground truth of every measurement we've made, yet it has
never been validated against a human. This samples a stratified set of answered
cases (slice E1/E3/E4 x lane {vector, graph-det}), STRIPS the judge verdict and
the lane (anti-anchoring), and writes a CSV the user labels correct/partial/
incorrect against the SAME gold the judge sees. A private key file maps each
cal_id back to its (slice, lane, _id) so we can join human labels with the panel
verdicts and compute human-vs-judge Cohen's kappa.

Blind by construction: the user sees only {question, gold, candidate} — not the
lane, not any model's verdict (§20: who holds ground truth matters; an anchored
human is not an anchor).

Run: python examples/contextgraph/build_calibration_set.py
"""
from __future__ import annotations
import csv, glob, json, random
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[2]
VEC_GLOB = str(ROOT / "outputs/evaluation/contextgraph/e1-bc3-a1/partial/*.json")
DET_GLOB = str(ROOT / "outputs/evaluation/contextgraph/e1-bc3-detgraph-v2/partial/*.json")
OUT_DIR = ROOT / "outputs/evaluation/contextgraph/calibration"
SLICES = ["E1_FACT", "E3_PROPOSALS", "E4_POSITIONS"]
PER_SLICE = 4  # base-ids per slice -> x2 lanes = 8 rows/slice = 24 total
SEED = 42


def _load(glob_pat, lane_filter=None):
    out = {}
    for f in glob.glob(glob_pat):
        d = json.load(open(f))
        if lane_filter and d.get("retrieval") != lane_filter:
            continue
        base = str(d.get("_id", "")).split("|")[0]
        if base:
            out[base] = d
    return out


def main():
    vec = _load(VEC_GLOB, lane_filter="vector")
    det = _load(DET_GLOB)  # all graph-det
    # base-ids present in BOTH lanes, per slice
    by_slice = defaultdict(list)
    for base, d in det.items():
        if base in vec and d.get("slice") in SLICES:
            by_slice[d["slice"]].append(base)
    rng = random.Random(SEED)
    rows, key = [], {}
    cal_n = 0
    for sl in SLICES:
        bases = sorted(by_slice[sl])
        rng.shuffle(bases)
        picked = bases[:PER_SLICE]
        for base in picked:
            for lane, src in (("vector", vec), ("graph-det", det)):
                d = src[base]
                cal_n += 1
                cal_id = f"C{cal_n:02d}"
                rows.append({
                    "cal_id": cal_id, "slice": sl,
                    "question": d.get("query", ""),
                    "gold_answer": d.get("expected_answer", ""),
                    "candidate_answer": d.get("answer", ""),
                    "your_verdict (correct|partial|incorrect)": "",
                })
                key[cal_id] = {"slice": sl, "lane": lane, "base_id": base,
                               "_id": d.get("_id", "")}
    rng.shuffle(rows)  # de-correlate cal_id order from slice/lane (extra blinding)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    blind = OUT_DIR / "human_calibration_BLIND.csv"
    with open(blind, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    (OUT_DIR / "_key.json").write_text(json.dumps(key, indent=2))
    print(f"wrote {len(rows)} blind calibration rows -> {blind.relative_to(ROOT)}")
    print(f"private key -> {(OUT_DIR / '_key.json').relative_to(ROOT)}")
    from collections import Counter
    print("strata:", dict(Counter((key[r['cal_id']]['slice'], key[r['cal_id']]['lane']) for r in rows)))
    print("\nFill the 'your_verdict' column (correct/partial/incorrect) vs the gold, then tell me.")


if __name__ == "__main__":
    main()
