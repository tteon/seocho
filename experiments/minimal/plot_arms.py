#!/usr/bin/env python3
"""The arm figure: does giving the extractor an ontology make two models agree?

One figure, two panels, because the result needs both to be honest.

  left    the headline. Comparable-key rate per arm under both key rules.
  right   why. More ontology produces more keys, and the extra keys are the
          ones no second model matches, so the rate falls as the vocabulary
          grows.

Design choices, and the reasons:

- The pre-registered direction is drawn on the plot as an arrow, so a reader
  sees immediately that the result runs against it rather than having to infer
  that from the caption (CLAUDE.md 20.4).
- Counts live on the right panel, not repeated on the left. A rate over 323
  keys and a rate over 1,334 are not the same evidence, so the counts have to
  appear somewhere; printing them twice only crowded the bars.
- The two panels share the arm order and colour, so the eye carries one mapping.
- Grey, not red/green. Nothing here is a success or a failure state; it is a
  measurement that came out the other way.

Writes PDF for the paper and PNG for reading.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "papers/log2026/figures"

ARM_LABEL = {
    "A": "A\nno ontology",
    "B": "B\nhand-written\n(20 classes)",
    "C": "C\nreal FIBO\n(70 classes)",
    "D": "D\nFIBO + synonyms\n(70 classes)",
}
INK = "#1a1a1a"
MUTED = "#8a8a8a"
FILL_NAME = "#4a4a4a"
FILL_SLUG = "#c4c4c4"
ACCENT = "#b4472e"


def latest_results() -> dict:
    candidates = sorted((ROOT / "outputs/minimal").glob("*-arm-results"),
                        reverse=True)
    for directory in candidates:
        path = directory / "arm_results.json"
        if path.is_file():
            return json.loads(path.read_text())
    raise SystemExit("no arm_results.json found; run arm_results.py first")


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    payload = latest_results()
    by_arm = payload["by_arm"]
    arms = [a for a in ("A", "B", "C", "D") if a in by_arm]

    name_rate = [by_arm[a]["by_key_rule"]["name"]["comparable_rate"] for a in arms]
    slug_rate = [by_arm[a]["by_key_rule"]["slug"]["comparable_rate"] for a in arms]
    keys = [by_arm[a]["by_key_rule"]["name"]["keys"] for a in arms]
    comparable = [by_arm[a]["by_key_rule"]["name"]["comparable"] for a in arms]

    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 9,
        "axes.edgecolor": MUTED, "axes.linewidth": 0.7,
        "axes.labelcolor": INK, "text.color": INK,
        "xtick.color": MUTED, "ytick.color": MUTED,
        "xtick.major.size": 0, "ytick.major.size": 3,
        "figure.dpi": 160,
    })

    fig, (left, right) = plt.subplots(1, 2, figsize=(9.6, 3.6),
                                      gridspec_kw={"width_ratios": [1.05, 1]})
    positions = range(len(arms))
    width = 0.36

    # ---- left: the headline -------------------------------------------------
    left.bar([p - width / 2 for p in positions], name_rate, width,
             color=FILL_NAME, label="keyed by name", zorder=3)
    left.bar([p + width / 2 for p in positions], slug_rate, width,
             color=FILL_SLUG, label="keyed by the model's own id", zorder=3)
    for p, (rate, count, total) in enumerate(zip(name_rate, comparable, keys)):
        left.text(p - width / 2, rate + 0.012, f"{rate:.3f}", ha="center",
                  fontsize=8, color=INK)
    for p, rate in enumerate(slug_rate):
        left.text(p + width / 2, rate + 0.012, f"{rate:.3f}", ha="center",
                  fontsize=8, color=MUTED)

    # The pre-registered expectation, drawn so the reader cannot miss that the
    # measurement went the other way.
    left.annotate("", xy=(3.15, 0.415), xytext=(-0.15, 0.415),
                  arrowprops=dict(arrowstyle="->", color=ACCENT, lw=1.1,
                                  linestyle=(0, (4, 2))))
    left.text(1.5, 0.428, "pre-registered direction: more ontology, more agreement",
              ha="center", fontsize=7.5, color=ACCENT, style="italic")

    left.set_xticks(list(positions))
    left.set_xticklabels([ARM_LABEL[a] for a in arms], fontsize=7.5)
    left.set_ylim(0, 0.47)
    left.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    left.set_ylabel("facts a second model also describes")
    left.set_title("Agreement between independently extracted views",
                   fontsize=9.5, loc="left", pad=10)
    left.legend(frameon=False, fontsize=7.5, loc="upper right",
                bbox_to_anchor=(1.02, 0.80))
    left.grid(axis="y", color="#e8e8e8", lw=0.6, zorder=0)
    for side in ("top", "right"):
        left.spines[side].set_visible(False)

    # ---- right: the mechanism ----------------------------------------------
    unmatched = [k - c for k, c in zip(keys, comparable)]
    right.bar(list(positions), comparable, 0.55, color=FILL_NAME,
              label="matched by a second model", zorder=3)
    right.bar(list(positions), unmatched, 0.55, bottom=comparable,
              color="#e4e4e4", edgecolor=MUTED, linewidth=0.5,
              label="seen by one model only", zorder=3)
    for p, (c, u) in enumerate(zip(comparable, unmatched)):
        right.text(p, c + u + 25, f"{c + u:,}", ha="center", fontsize=8,
                   color=INK)
    right.set_ylim(0, 2700)
    right.text(-0.45, 2650, "70 classes produce 25% more distinct names than none "
                        "at all,\nand almost all of the extra ones go unmatched",
               fontsize=7.5, color=ACCENT, va="top", style="italic")
    right.set_xticks(list(positions))
    right.set_xticklabels(arms, fontsize=9)
    right.set_ylabel("distinct fact names extracted")
    right.set_title("Where the extra names go", fontsize=9.5, loc="left", pad=10)
    right.legend(frameon=False, fontsize=7.5, loc="upper left",
                 bbox_to_anchor=(0.0, 0.80))
    right.grid(axis="y", color="#e8e8e8", lw=0.6, zorder=0)
    for side in ("top", "right"):
        right.spines[side].set_visible(False)

    caption = ("16 cases x 3 extractor models (DeepSeek-V3.1, gpt-oss-120b, MiniMax-M2.7), "
               "192/192 extractions scored, no case lost to fallback. Documents, prompt, "
               "chunking and seed held fixed; only the ontology handed to the extractor moves.")
    fig.text(0.008, -0.045, caption, fontsize=7, color=MUTED, ha="left", va="top",
             wrap=True)

    fig.tight_layout(rect=(0, 0.035, 1, 1))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for suffix in ("pdf", "png"):
        path = OUT_DIR / f"arm_comparability.{suffix}"
        fig.savefig(path, bbox_inches="tight", facecolor="white")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
