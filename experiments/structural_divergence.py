#!/usr/bin/env python3
"""Anchored cross-view structural divergence, frozen as an artifact.

SimRank-style structural comparison across independently extracted graphs
presupposes knowing which node in view 1 is which node in view 2 — and this
study's central result is that names cannot supply that identity. Provenance
anchors can: two nodes anchored to the same source token are the same fact
by construction. This script measures, per extraction condition, how similar
the SAME anchored entity's neighborhood is across views (Jaccard over the
anchor keys of its neighbors), against a matched null of unrelated
cross-view entity pairs in the same case.

Why a matched null is mandatory here: this repository has killed one
structural trigger already (SDCR's PageRank divergence — the null itself
saturated and every threshold equalled 1.000). The usable quantity is the
ratio of same-anchor similarity to the null, not the raw score. A ratio
near 1 means structural methods (SimRank, GNN alignment) have no signal on
this corpus and condition.

Context relevant to reading the number: views are extracted under full
context isolation (per-case workspaces, per-model databases), so no shared
structure can leak between views through the store; and the comparison uses
only extraction outputs, never gold answers, so it is contamination-free by
construction.

    python3 experiments/structural_divergence.py --pairs s1:A,s2:C
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/minimal"))

import observe  # noqa: E402

MODELS = ("deepseek", "gptoss", "minimax27")
NULL_DRAWS_PER_CASE = 20


def anchors_for(tag: str) -> dict[tuple[str, str], dict[str, tuple]]:
    out: dict[tuple[str, str], dict[str, tuple]] = defaultdict(dict)
    for line in (ROOT / "snapshots" / tag / "anchors.jsonl").open():
        record = json.loads(line)
        if record.get("kind") == "anchor":
            out[(record["model"], record["case"])][record["eid"]] = (
                record["passage"], record["offset"])
    return out


def load_view(path: Path) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for line in path.open():
        record = json.loads(line)
        if record["kind"] == "edge":
            adjacency[record["source"]].add(record["target"])
            adjacency[record["target"]].add(record["source"])
    return adjacency


def measure(tag: str, letter: str) -> dict[str, Any]:
    anchor_map = anchors_for(tag)
    case_ids = sorted({p.name.split("_")[-1].split(".")[0]
                       for p in (ROOT / "snapshots" / tag).glob(
                           f"{letter}_deepseek_*.jsonl")})
    same: list[float] = []
    null: list[float] = []
    rng = random.Random(42)
    for cid in case_ids:
        views: dict[str, dict[tuple, frozenset]] = {}
        for model in MODELS:
            path = ROOT / "snapshots" / tag / f"{letter}_{model}_{cid}.jsonl"
            if not path.exists():
                continue
            adjacency = load_view(path)
            anchored = anchor_map.get((model, cid), {})
            views[model] = {
                anchor: frozenset(anchored[n] for n in adjacency.get(eid, ())
                                  if n in anchored)
                for eid, anchor in anchored.items()}
        model_keys = sorted(views)
        for i in range(len(model_keys)):
            for j in range(i + 1, len(model_keys)):
                left, right = views[model_keys[i]], views[model_keys[j]]
                shared = set(left) & set(right)
                for anchor in shared:
                    a, b = left[anchor], right[anchor]
                    if a or b:
                        same.append(len(a & b) / len(a | b))
                pool_l, pool_r = list(left), list(right)
                if pool_l and pool_r:
                    for _ in range(min(len(shared), NULL_DRAWS_PER_CASE)):
                        x, y = rng.choice(pool_l), rng.choice(pool_r)
                        if x == y:
                            continue
                        a, b = left[x], right[y]
                        if a or b:
                            null.append(len(a & b) / len(a | b))
    return {
        "cases": len(case_ids),
        "same_anchor": {"n": len(same),
                        "mean": round(statistics.mean(same), 4),
                        "median": round(statistics.median(same), 4),
                        "disjoint_share": round(
                            sum(1 for x in same if x == 0) / len(same), 4)},
        "matched_null": {"n": len(null),
                         "mean": round(statistics.mean(null), 4)},
        "signal_ratio": round(statistics.mean(same)
                              / max(statistics.mean(null), 1e-9), 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", default="s1:A,s2:C",
                        help="comma-separated tag:condition pairs")
    args = parser.parse_args()
    pairs = [p.split(":") for p in args.pairs.split(",") if p]

    run = observe.Run(ROOT / "outputs/minimal", "structural-divergence", {
        "contract": "log2026.structural_divergence.v1",
        "decisive": {"pairs": args.pairs, "seed": 42,
                     "null": "unrelated cross-view entity pairs, same case",
                     "neighborhood": "anchor keys of anchored neighbors"},
    })
    result: dict[str, Any] = {}
    for tag, letter in pairs:
        with run.stage(f"measure.{tag}{letter}", tag=tag,
                       condition=letter) as out:
            payload = measure(tag, letter)
            out.update(payload)
            result[f"{tag}:{letter}"] = payload

    artifact = {
        "contract": "log2026.structural_divergence.v1",
        "question": ("Do independently extracted views embed the same "
                     "source fact in similar graph structure — is there "
                     "signal for structural cross-view methods at all?"),
        "claim_boundary": (
            "Node identity across views comes from provenance anchors, so "
            "only anchored entities are measured. The reportable quantity "
            "is the ratio of same-anchor neighborhood similarity to a "
            "matched null; raw similarity alone is uninterpretable "
            "(SDCR's PageRank trigger died of a saturated null). Views are "
            "extracted under per-case, per-model isolation, and the "
            "measure never reads gold answers, so it is contamination-free "
            "by construction."),
        **result,
    }
    (run.dir / "structural_divergence.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=1))
    run.finish(result)
    for key, payload in result.items():
        print(f"{key}: same {payload['same_anchor']['mean']} "
              f"null {payload['matched_null']['mean']} "
              f"ratio {payload['signal_ratio']} "
              f"disjoint {payload['same_anchor']['disjoint_share']:.1%}")


if __name__ == "__main__":
    main()
