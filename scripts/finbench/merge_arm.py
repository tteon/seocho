"""Replace one arm's episodes in an interaction run with those from a re-run.

Needed because the plan arm was rebuilt after the first full run: it originally gated on the
planner's estimated row count, and that turned out not to be a cost signal at all — measured
across the 48 queries settled on at SF100, actual db hits ran from 2.9x to 4,617,254x the
summed EstimatedRows, so the gate never fired once in 108 episodes. The rebuilt arm gates on
measured elapsed time instead. Only that arm was re-run; re-running the other three would
change nothing and would cost the comparison its shared conditions.

The superseded episodes are kept in the output under ``superseded`` rather than deleted, so
the negative result about EstimatedRows stays checkable against the data that produced it.

Usage:
  python scripts/finbench/merge_arm.py --base outputs/finbench/agent_interaction.json \
      --replacement /path/to/plan_arm.json --arm plan
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", required=True)
    p.add_argument("--replacement", required=True)
    p.add_argument("--arm", required=True)
    p.add_argument("--out", default=None)
    p.add_argument("--reason", default="")
    args = p.parse_args()

    with Path(args.base).open('r', encoding='utf-8') as f:

        base = json.load(f)
    with Path(args.replacement).open('r', encoding='utf-8') as f:
        repl = json.load(f)

    superseded = [e for e in base["episodes"] if e["arm"] == args.arm]
    kept = [e for e in base["episodes"] if e["arm"] != args.arm]
    incoming = [e for e in repl["episodes"] if e["arm"] == args.arm]
    if not incoming:
        raise SystemExit(f"the replacement run holds no episodes for arm {args.arm!r}")

    base["episodes"] = sorted(kept + incoming,
                              key=lambda e: (e["sf"], e["arm"], e["question_id"], e["repeat"]))
    base["superseded"] = superseded
    base["probe_timeout_s"] = repl.get("probe_timeout_s")
    base.setdefault("notes", []).append(
        args.reason or
        f"the {args.arm!r} arm was re-run after being rebuilt; {len(superseded)} superseded "
        f"episodes are kept under 'superseded'")

    out = Path(args.out or args.base)
    out.write_text(json.dumps(base, indent=1, default=str))
    print(f"replaced {len(superseded)} episodes with {len(incoming)} for arm {args.arm!r} "
          f"-> {out}")


if __name__ == "__main__":
    main()
