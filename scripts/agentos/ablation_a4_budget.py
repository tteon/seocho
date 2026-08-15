"""Ablation A4 — resources: token-budget containment, OFF vs ON.

Ablation row A4 of the OS study (wiki/os-ablation-study-design.md, seocho-4rb).
The resource subsystem's axis: does a runaway agent chain stop, and how far past
the cap does it get? We run the same multi-turn chain through the real
``TokenBudgetTracker`` (the one wired into the OS via RunHooks, ADR-0153) with
the budget OFF (0 = unlimited, a bare agent) vs ON (a per-session cap). Metrics:
tokens spent, whether the run halted, and overshoot past the cap.

Usage:
  python scripts/agentos/ablation_a4_budget.py \
      --turns 40 --per-turn 800 --budget 10000 \
      --out outputs/agentos/ablation_a4_budget.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from seocho.budget import BudgetExceededError, TokenBudgetTracker  # noqa: E402


def run_chain(*, budget: int, turns: int, per_turn: int) -> Dict[str, Any]:
    """Simulate a chain of turns, each charging `per_turn` completion tokens,
    exactly as the OS's on_llm_end hook charges the tracker per turn."""
    tracker = TokenBudgetTracker(budget=budget, scope="ablation-a4")
    halted_at = None
    for turn in range(1, turns + 1):
        try:
            tracker.charge(completion=per_turn)
        except BudgetExceededError:
            halted_at = turn
            break
    spent = tracker.total
    return {
        "budget": budget,
        "halted": halted_at is not None,
        "halted_at_turn": halted_at,
        "turns_run": halted_at if halted_at is not None else turns,
        "tokens_spent": spent,
        # Overshoot: how far past the cap the last charged turn pushed us.
        "overshoot": max(0, spent - budget) if budget > 0 else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--turns", type=int, default=40)
    ap.add_argument("--per-turn", type=int, default=800)
    ap.add_argument("--budget", type=int, default=10000)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    off = run_chain(budget=0, turns=args.turns, per_turn=args.per_turn)
    on = run_chain(budget=args.budget, turns=args.turns, per_turn=args.per_turn)
    report = {"turns": args.turns, "per_turn": args.per_turn,
              "cap": args.budget, "off": off, "on": on}

    print("=== A4 budget containment: OFF (no budget) vs ON (cap) ===")
    print(f"  chain: {args.turns} turns x {args.per_turn} tok "
          f"(would spend {args.turns * args.per_turn} unchecked); cap = {args.budget}")
    print(f"  OFF  halted={off['halted']!s:5s} turns={off['turns_run']:2d} "
          f"spent={off['tokens_spent']}")
    print(f"  ON   halted={on['halted']!s:5s} turns={on['turns_run']:2d} "
          f"spent={on['tokens_spent']}  overshoot={on['overshoot']} "
          f"(<= one turn = {args.per_turn})")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
