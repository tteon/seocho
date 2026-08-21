"""Turn the interaction and replay JSON into the tables a reader can act on.

Four tables, in the order a reader needs them:

  1. the headline — cost and correctness per arm at each scale, which is the "does the agent
     design still matter as the graph grows" answer in one block;
  2. p99 by question and arm, which is the same answer per question rather than averaged, and
     the only place where a design that helps one audience and hurts the other is visible;
  3. what the guardrail and the plan gate actually rejected, by reason, because a rejection
     count without its reasons cannot be acted on;
  4. every cell where the agent produced more than one distinct query across repeats at
     temperature zero — an agent design that is not reproducible has no single tail.

Usage:
  python scripts/finbench/report_interaction.py > docs/execplans/finbench-agent-interaction.md
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

ARM_ORDER = ["labels", "ontology", "guardrail", "plan"]
ARM_LABEL = {"labels": "labels only", "ontology": "+ ontology",
             "guardrail": "+ guardrail", "plan": "+ plan feedback"}


def fmt(n: Any, width: int = 0) -> str:
    if n is None:
        return "—"
    if isinstance(n, float):
        return f"{n:,.1f}".rjust(width)
    if isinstance(n, int):
        return f"{n:,}".rjust(width)
    return str(n).rjust(width)


def scored(episodes):
    """Episodes that can be scored at all.

    An episode whose reference query never completed has `score_correct = None`. Counting it as
    a failure would charge the agent for the database's limit, and counting it as a success
    would be worse; it is excluded from the numerator and the denominator both, and the count
    of exclusions is reported separately.
    """
    return [e for e in episodes if e.get("score_correct") is not None]

def headline(episodes: List[Dict[str, Any]]) -> str:
    sfs = sorted({e["sf"] for e in episodes})
    lines = ["| scale | agent design | correct | db hits (median) | round trips | "
             "chars into context | guardrail rejects | plan rejects | timeouts |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for sf in sfs:
        for arm in ARM_ORDER:
            sel = [e for e in episodes if e["sf"] == sf and e["arm"] == arm]
            if not sel:
                continue
            sel = scored(sel)
            if not sel:
                continue
            correct = sum(e["score_correct"] for e in sel)
            lines.append(
                f"| SF{sf} | {ARM_LABEL[arm]} | {correct}/{len(sel)} "
                f"| {fmt(int(statistics.median(e['db_hits'] for e in sel)))} "
                f"| {statistics.median(e['round_trips'] for e in sel):.1f} "
                f"| {fmt(int(statistics.median(e['chars_into_context'] for e in sel)))} "
                f"| {sum(e['guardrail_rejections'] for e in sel)} "
                f"| {sum(e['plan_rejections'] for e in sel)} "
                f"| {sum(e['timeouts'] for e in sel)} |")
    return "\n".join(lines)


def p99_table(cells: List[Dict[str, Any]]) -> str:
    sfs = sorted({c["sf"] for c in cells})
    by = {(c["question_id"], c["arm"], c["sf"]): c for c in cells}
    qids = sorted({c["question_id"] for c in cells},
                  key=lambda q: (q.split("_")[0], ["easy", "med", "hard"].index(q.split("_")[1]), q))
    header = "| question | agent design | " + " | ".join(f"SF{s} p99 (ms)" for s in sfs) + " |"
    lines = [header, "|---|---|" + "---:|" * len(sfs)]
    for qid in qids:
        for arm in ARM_ORDER:
            cols = []
            for sf in sfs:
                c = by.get((qid, arm, sf))
                if not c:
                    cols.append("—")
                elif not c.get("ok"):
                    cols.append("no query")
                else:
                    mark = "" if c["correct_rate"] >= 1.0 else (
                        " ✗" if c["correct_rate"] == 0 else f" ({c['correct_rate']:.0%})")
                    cols.append(f"{c['server_p99']:,.0f}{mark}")
            lines.append(f"| {qid} | {ARM_LABEL[arm]} | " + " | ".join(cols) + " |")
    lines.append("")
    lines.append("✗ marks a cell whose answer never matched gold; a percentage marks partial "
                 "agreement across repeats. A fast wrong answer is not a result.")
    return "\n".join(lines)


def rejections(episodes: List[Dict[str, Any]]) -> str:
    tally: Dict[str, Counter] = defaultdict(Counter)
    for e in episodes:
        for v in e.get("violations") or []:
            tally[e["arm"]][str(v).split(":", 1)[0]] += 1
    if not tally:
        return "_No guardrail rejection fired in this run._"
    lines = ["| agent design | rejection reason | count |", "|---|---|---:|"]
    for arm in ARM_ORDER:
        for reason, n in tally.get(arm, Counter()).most_common():
            lines.append(f"| {ARM_LABEL[arm]} | `{reason}` | {n} |")
    return "\n".join(lines)


def instability(cells: List[Dict[str, Any]]) -> str:
    unstable = [c for c in cells if c.get("variants", 0) > 1]
    if not unstable:
        return ("_Every cell settled on one query across all repeats._ Temperature is 0, so "
                "this is the expected outcome; the repeats are here to catch the cells where "
                "it does not hold.")
    lines = ["| scale | question | agent design | distinct queries | correct |",
             "|---|---|---|---:|---:|"]
    for c in sorted(unstable, key=lambda c: (-c["variants"], c["sf"])):
        lines.append(f"| SF{c['sf']} | {c['question_id']} | {ARM_LABEL[c['arm']]} "
                     f"| {c['variants']} | {c['correct_rate']:.0%} |")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--episodes", default="outputs/finbench/agent_interaction.json")
    p.add_argument("--replay", default="outputs/finbench/replay_p99.json")
    args = p.parse_args()

    run = json.loads(Path(args.episodes).read_text())
    episodes = run["episodes"]
    replay_path = Path(args.replay)
    cells = json.loads(replay_path.read_text())["cells"] if replay_path.exists() else []

    print("# Agent ↔ database interaction, by audience, difficulty, scale and agent design\n")
    print(f"Model `{run['model']}` · {len(episodes)} episodes · {run.get('repeats')} repeats "
          f"per cell · row cap {run['row_cap']} · transaction timeout {run['tx_timeout_s']:.0f}s "
          f"· plan-gate probe {run.get('probe_timeout_s')}s.\n")
    print("Latency figures come from replaying the query each design settled on 100 times with "
          "no model in the loop, first execution discarded. That is a deliberate split: a p99 "
          "needs about a hundred samples per cell, and the number an operator is on the hook "
          "for is the tail of the query a design ships, not the variance of the model that "
          "wrote it.\n")

    print("## The questions\n")
    print("| id | audience | difficulty | asked as |")
    print("|---|---|---|---|")
    for q in run["questions"]:
        print(f"| `{q['id']}` | {q['audience']} | {q['difficulty']} | {q['ko']} |")

    print("\n## 1. Cost and correctness per agent design\n")
    print(headline(episodes))
    print("\n## 2. p99 latency by question\n")
    print(p99_table(cells) if cells else "_Replay has not been run yet._")
    print("\n## 3. What the guardrail rejected\n")
    print(rejections(episodes))
    print("\n## 4. Cells where the design did not reproduce\n")
    print(instability(cells) if cells else "_Replay has not been run yet._")


if __name__ == "__main__":
    main()
