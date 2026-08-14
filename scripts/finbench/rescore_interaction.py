"""Re-score an interaction run against gold that the run itself could not compute in time.

At SF100 the reference query for the three-layer conjunction (`int_hard_1`) does not finish
inside the run's gold timeout. That is a result in its own right — the hand-written, optimal
query for the hardest question no longer returns at that scale — but it must not be allowed to
become an empty gold, because an empty gold silently marks an agent that returned nothing as
correct.

This script takes gold computed separately with a long timeout and re-scores the affected
episodes, recording how long the reference took so the cost of knowing the answer is on the
record next to the answer.

Usage:
  python scripts/finbench/rescore_interaction.py \
      --episodes outputs/finbench/agent_interaction.json \
      --gold int_hard_1:100:/path/to/gold_int_hard_1_sf100.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_interaction import QUESTIONS, parse_answer, score  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--episodes", default="outputs/finbench/agent_interaction.json")
    p.add_argument("--gold", nargs="+", required=True,
                   help="question_id:sf:path — the path holds {'seconds': float, 'rows': [...]}")
    p.add_argument("--out", default=None, help="defaults to overwriting --episodes")
    p.add_argument("--no-gold", nargs="*", default=[],
                   help="question_id:sf pairs whose reference query does not complete. Their "
                        "episodes are marked unscoreable instead of scored against an empty "
                        "gold, which would mark an agent that returned nothing as correct.")
    p.add_argument("--no-gold-reason", default="",
                   help="what was tried and how long it ran, recorded on every marked episode")
    args = p.parse_args()

    run = json.loads(Path(args.episodes).read_text())
    by_id = {q["id"]: q for q in QUESTIONS}

    overrides = {}
    for spec in args.gold:
        qid, sf, path = spec.split(":", 2)
        payload = json.loads(Path(path).read_text())
        overrides[(qid, int(sf))] = payload
        print(f"gold for {qid} at SF{sf}: {len(payload['rows'])} rows, "
              f"reference query took {payload['seconds']:,.1f}s")

    patched = 0
    for e in run["episodes"]:
        key = (e["question_id"], e["sf"])
        if key not in overrides:
            continue
        payload = overrides[key]
        answer, _note = parse_answer(e.get("final_output") or "")
        verdict = score(by_id[e["question_id"]], payload["rows"], answer)
        for k in [k for k in e if k.startswith("score_")]:
            del e[k]
        e.update({f"score_{k}": v for k, v in verdict.items()})
        e["gold_reference_seconds"] = payload["seconds"]
        e["gold_source"] = "recomputed with an extended timeout"
        patched += 1

    unscoreable = 0
    for spec in args.no_gold:
        qid, sf = spec.split(":")
        for e in run["episodes"]:
            if e["question_id"] != qid or e["sf"] != int(sf):
                continue
            for k in [k for k in e if k.startswith("score_")]:
                del e[k]
            # Not False. There is no ground truth to be wrong against, and counting these as
            # failures would attribute the database's limit to the agent.
            e["score_correct"] = None
            e["score_note"] = "no ground truth obtainable"
            e["gold_unobtainable_reason"] = args.no_gold_reason
            unscoreable += 1
    if unscoreable:
        print(f"marked {unscoreable} episodes unscoreable: {args.no_gold}")
        run.setdefault("notes", []).append(
            f"unscoreable (no ground truth obtainable): {args.no_gold}. "
            f"{args.no_gold_reason}")

    run.setdefault("notes", []).append(
        "int_hard_1 gold at SF100 was recomputed outside the run: the reference query exceeds "
        "the in-run gold timeout, and scoring against an empty gold would have marked an agent "
        "that returned nothing as correct.")
    out = Path(args.out or args.episodes)
    out.write_text(json.dumps(run, indent=1, default=str))
    print(f"re-scored {patched} episodes -> {out}")


if __name__ == "__main__":
    main()
