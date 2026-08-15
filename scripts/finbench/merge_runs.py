"""Fold episodes from a separate run into a base run, adding conditions rather than replacing.

`merge_arm.py` swaps one condition's episodes for a re-run of the same condition. This does the
other thing: the in-context pair was measured after the first four conditions had already run,
against the same graphs and the same questions, so its episodes belong in the same file rather
than in a second one a reader has to join by hand.

Conditions that already exist in the base are refused rather than silently overwritten — a
merge that quietly replaces a condition is how two runs with different settings end up
averaged together and reported as one.

Usage:
  python scripts/finbench/merge_runs.py --base outputs/finbench/agent_interaction.json \
      --add /path/to/incontext_ab.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", required=True)
    p.add_argument("--add", required=True)
    p.add_argument("--out", default=None)
    p.add_argument("--note", default="")
    args = p.parse_args()

    base = json.loads(Path(args.base).read_text())
    extra = json.loads(Path(args.add).read_text())

    have = {e["arm"] for e in base["episodes"]}
    incoming = {e["arm"] for e in extra["episodes"]}
    clash = have & incoming
    if clash:
        raise SystemExit(
            f"the base already holds condition(s) {sorted(clash)}; use merge_arm.py to replace "
            f"a condition, not this")

    # The two runs must have measured the same graphs, or their scale axes are not comparable.
    base_ctx = {db: c.get("anchor") for db, c in base.get("context", {}).items()}
    add_ctx = {db: c.get("anchor") for db, c in extra.get("context", {}).items()}
    for db, anchor in add_ctx.items():
        if db in base_ctx and base_ctx[db] != anchor:
            raise SystemExit(f"{db} was anchored on {base_ctx[db]} in the base and {anchor} in "
                             f"the addition; the runs are not comparable")
    if base.get("model") != extra.get("model"):
        raise SystemExit(f"model differs: {base.get('model')} vs {extra.get('model')}")

    known = {q["id"] for q in base.get("questions", [])}
    base["questions"] = base.get("questions", []) + [
        q for q in extra.get("questions", []) if q["id"] not in known]
    base["episodes"] = sorted(base["episodes"] + extra["episodes"],
                              key=lambda e: (e["sf"], e["arm"], e["question_id"], e["repeat"]))
    base["arms"] = list(dict.fromkeys(list(base.get("arms", [])) + list(extra.get("arms", []))))
    for key in ("probe_timeout_s", "in_context_row_cap", "in_context_max_turns"):
        if key in extra and key not in base:
            base[key] = extra[key]
    base.setdefault("notes", []).append(
        args.note or f"merged {len(extra['episodes'])} episodes for condition(s) "
                     f"{sorted(incoming)} from {args.add}")

    out = Path(args.out or args.base)
    out.write_text(json.dumps(base, indent=1, default=str))
    print(f"merged {len(extra['episodes'])} episodes for {sorted(incoming)} -> {out} "
          f"({len(base['episodes'])} total, conditions {base['arms']})")


if __name__ == "__main__":
    main()
