"""Charts for the agent<->database interaction experiment.

Three figures, written to docs/figures/:

  ``agent-p99-by-question.svg``  the main one. Twelve panels, one per question, laid out as
      audience x difficulty. Within each panel: scale factor across, p99 latency up, one line
      per agent design. This is the figure that answers "as the graph grows, does the agent
      design still matter, and for which questions".
  ``agent-cost-by-arm.svg``      db hits and round trips per answered question, by arm and
      scale. db hits rather than milliseconds because it is the one cost unit unaffected by
      what else is running on the box.
  ``agent-accuracy-by-cell.svg`` correctness by audience, difficulty and arm at each scale.
      A latency chart without this beside it would reward an agent design that is fast because
      it answers the wrong question cheaply.

Latency here is ``server_p99``: the database's own timing, over 100 replays of the query each
agent design settled on, first execution discarded. It excludes the model, which is deliberate
— the model's contribution is round trips, and that is charted separately, because the two
scale for entirely different reasons.

Usage:
  python scripts/finbench/plot_interaction.py \
      --replay outputs/finbench/replay_p99.json \
      --episodes outputs/finbench/agent_interaction.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any, Dict, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager  # noqa: F401  (populates fontManager.ttflist)
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

ARM_ORDER = ["labels", "ontology", "guardrail", "plan"]
ARM_LABEL = {
    "labels": "labels only",
    "ontology": "+ ontology",
    "guardrail": "+ guardrail",
    "plan": "+ plan feedback",
}
# Sequential rather than categorical: the arms are cumulative, so the eye should read them as
# a progression and not as four unrelated options.
ARM_COLOR = {"labels": "#c2410c", "ontology": "#ca8a04",
             "guardrail": "#0e7490", "plan": "#15803d"}
ARM_MARKER = {"labels": "o", "ontology": "s", "guardrail": "^", "plan": "D"}

AUDIENCES = ["external", "internal"]
DIFFICULTIES = ["easy", "medium", "hard"]
AUD_TITLE = {"external": "External · public-facing service",
             "internal": "Internal · AML investigator"}

# The Korean gloss under each panel is the question as it would actually be asked, so the
# font has to render it. DejaVu carries no Hangul and drops the glyphs silently, leaving
# boxes where the question should be.
_KO_FONTS = [f for f in ("Noto Sans CJK KR", "NanumGothic", "Malgun Gothic")
             if f in {fp.name for fp in matplotlib.font_manager.fontManager.ttflist}]

plt.rcParams.update({
    "font.family": (_KO_FONTS[:1] or ["DejaVu Sans"]) + ["DejaVu Sans"],
    "font.size": 8.5,
    "axes.unicode_minus": False,
    "axes.edgecolor": "#c9ced6",
    "axes.linewidth": 0.8,
    "axes.grid": True,
    "grid.color": "#eceff3",
    "grid.linewidth": 0.7,
    "figure.facecolor": "white",
    "savefig.facecolor": "white",
})


def _panel_key(q: Dict[str, Any]) -> str:
    return f"{q['audience']}/{q['difficulty']}"


def scored(episodes):
    """Episodes that can be scored at all.

    An episode whose reference query never completed has `score_correct = None`. Counting it as
    a failure would charge the agent for the database's limit, and counting it as a success
    would be worse; it is excluded from the numerator and the denominator both.
    """
    return [e for e in episodes if e.get("score_correct") is not None]


def _face(rate, color):
    """Marker fill encodes correctness, including the case where correctness is unknowable.

    `None` is not zero. At SF1000 the reference query for the three-layer conjunction does not
    complete in two hours, so there is no ground truth to be right or wrong against; drawing
    those points as failures would charge the agent for the database's limit. They are drawn
    faint and unfilled instead.
    """
    if rate is None:
        return "none", 0.4
    if rate >= 1.0:
        return color, 1.0
    if rate <= 0:
        return "white", 1.0
    return "#d9dde3", 1.0


def plot_p99(cells: List[Dict[str, Any]], questions: List[Dict[str, Any]], out: Path) -> None:
    qmeta = {q["id"]: q for q in questions}
    ordered = sorted(
        qmeta.values(),
        key=lambda q: (AUDIENCES.index(q["audience"]), DIFFICULTIES.index(q["difficulty"]),
                       q["id"]))
    sfs = sorted({c["sf"] for c in cells})

    by: Dict[Any, Dict[str, Any]] = {(c["question_id"], c["arm"], c["sf"]): c for c in cells}

    ncols = 3
    nrows = -(-len(ordered) // ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(11.5, 3.0 * nrows + 1.6), sharex=True)
    fig.subplots_adjust(hspace=0.62, wspace=0.26, top=1 - 1.35 / (3.0 * nrows + 1.6),
                        bottom=0.75 / (3.0 * nrows + 1.6), left=0.085, right=0.975)

    for idx, q in enumerate(ordered):
        ax = axes[idx // ncols][idx % ncols]
        for arm in ARM_ORDER:
            xs, ys, right, miss = [], [], [], []
            for sf in sfs:
                c = by.get((q["id"], arm, sf))
                if c and c.get("ok") and c.get("server_p99") is not None:
                    xs.append(sf)
                    # A p99 of 0 ms cannot be drawn on a log axis; the database reports whole
                    # milliseconds, so sub-millisecond queries land there legitimately.
                    ys.append(max(float(c["server_p99"]), 0.5))
                    right.append(c.get("correct_rate", 0.0))
                else:
                    miss.append(sf)
            if xs:
                ax.plot(xs, ys, linewidth=1.5, color=ARM_COLOR[arm], label=ARM_LABEL[arm],
                        zorder=3)
                # Marker fill carries correctness, because a latency chart alone rewards the
                # design that is fast by answering a cheaper question than the one asked —
                # which is exactly what happens on int_hard_1.
                for x, y, r in zip(xs, ys, right):
                    fc, alpha = _face(r, ARM_COLOR[arm])
                    ax.plot([x], [y], marker=ARM_MARKER[arm], markersize=5.2,
                            markerfacecolor=fc, markeredgecolor=ARM_COLOR[arm],
                            markeredgewidth=1.3, alpha=alpha, linestyle="none", zorder=4)
            for sf in miss:
                # An x marks a cell where the agent never got a query to run — a real outcome,
                # and one a gap in the line would hide.
                ax.plot([sf], [ax.get_ylim()[1]], marker="x", markersize=5,
                        color=ARM_COLOR[arm], zorder=5, clip_on=False)

        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xticks(sfs)
        ax.set_xticklabels([f"SF{s}" for s in sfs])
        # Every panel labels its own x axis: with a ragged final row, sharex would strip the
        # labels from whichever columns do not reach the bottom.
        ax.tick_params(axis="both", labelsize=7.5, length=2.5, labelbottom=True)
        ax.set_title(f"{q['id'].replace('_', ' ')}  ·  {q['difficulty']}",
                     fontsize=8.5, pad=4, loc="left", color="#12151a")
        ax.text(0.0, 1.005, "", transform=ax.transAxes)
        wrapped = q["ko"] if len(q["ko"]) <= 34 else q["ko"][:33] + "…"
        ax.set_xlabel(wrapped, fontsize=7.0, color="#6b7684", labelpad=3)
        if idx % ncols == 0:
            ax.set_ylabel("p99 latency (ms, log)", fontsize=8)

    for ax in axes.flat[len(ordered):]:
        ax.set_visible(False)

    # Audience bands, so the split the questions were written around is visible in the layout
    # rather than only in the ids. Placed from the first panel of each audience, since the
    # question count per audience is not fixed.
    first_row = {}
    for idx, q in enumerate(ordered):
        first_row.setdefault(q["audience"], idx // ncols)
    for aud, row in first_row.items():
        pos = axes[row][0].get_position()
        fig.text(0.085, pos.y1 + 0.030 / nrows + 0.008, AUD_TITLE[aud], fontsize=10.5,
                 weight="bold", color="#12151a", va="bottom")

    h = 3.0 * nrows + 1.6
    fig.suptitle("p99 query latency by question, scale and agent design",
                 fontsize=13, weight="bold", x=0.085, ha="left", y=1 - 0.30 / h,
                 color="#12151a")
    fig.text(0.085, 1 - 0.62 / h,
             "Up to 100 replays of the query each design settled on, first execution "
             "discarded; server-side timing, model excluded. Filled marker = every repeat "
             "matched gold, hollow = none did, grey = some.",
             fontsize=8, color="#6b7684", ha="left")
    handles = [Line2D([], [], color=ARM_COLOR[a], marker=ARM_MARKER[a], markersize=5,
                      linewidth=1.5, label=ARM_LABEL[a]) for a in ARM_ORDER]
    handles += [
        Line2D([], [], color="#47515f", marker="o", markerfacecolor="#47515f",
               linestyle="none", markersize=5, label="answer correct"),
        Line2D([], [], color="#47515f", marker="o", markerfacecolor="white",
               markeredgewidth=1.3, linestyle="none", markersize=5, label="answer wrong"),
        Line2D([], [], color="#47515f", marker="o", markerfacecolor="none", alpha=0.4,
               markeredgewidth=1.3, linestyle="none", markersize=5,
               label="no ground truth obtainable"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=6, frameon=False,
               fontsize=8.5, bbox_to_anchor=(0.53, 0.004))
    fig.savefig(out, format="svg")
    plt.close(fig)
    print(f"wrote {out}")


def plot_cost(episodes: List[Dict[str, Any]], out: Path) -> None:
    sfs = sorted({e["sf"] for e in episodes})
    fig, axes = plt.subplots(1, 3, figsize=(11.5, 3.9))
    fig.subplots_adjust(top=0.76, bottom=0.20, left=0.075, right=0.985, wspace=0.30)

    panels = [
        ("db_hits", "db hits per question (log)", True),
        ("round_trips", "Cypher round trips per question", False),
        ("chars_into_context", "characters returned into context (log)", True),
    ]
    for ax, (field, ylabel, logy) in zip(axes, panels):
        for arm in ARM_ORDER:
            xs, ys = [], []
            for sf in sfs:
                vals = [e[field] for e in episodes if e["arm"] == arm and e["sf"] == sf]
                if vals:
                    xs.append(sf)
                    ys.append(max(statistics.median(vals), 0.5 if logy else 0))
            ax.plot(xs, ys, marker=ARM_MARKER[arm], markersize=4.5, linewidth=1.6,
                    color=ARM_COLOR[arm], label=ARM_LABEL[arm])
        ax.set_xscale("log")
        if logy:
            ax.set_yscale("log")
        ax.set_xticks(sfs)
        ax.set_xticklabels([f"SF{s}" for s in sfs])
        ax.set_ylabel(ylabel, fontsize=8.5)
        ax.tick_params(labelsize=8, length=2.5)

    fig.suptitle("What the exchange costs, by agent design and scale",
                 fontsize=12.5, weight="bold", x=0.075, ha="left", y=0.955, color="#12151a")
    fig.text(0.075, 0.885,
             "Median across all twelve questions. db hits is the primary unit: it is the only "
             "one unaffected by concurrent load.",
             fontsize=8, color="#6b7684", ha="left")
    axes[0].legend(frameon=False, fontsize=8, loc="upper left")
    fig.savefig(out, format="svg")
    plt.close(fig)
    print(f"wrote {out}")


def plot_accuracy(episodes: List[Dict[str, Any]], out: Path) -> None:
    sfs = sorted({e["sf"] for e in episodes})
    fig, axes = plt.subplots(2, 3, figsize=(11.5, 6.2), sharey=True)
    fig.subplots_adjust(top=0.80, bottom=0.13, left=0.075, right=0.985,
                        hspace=0.42, wspace=0.14)

    width = 0.20
    for r, aud in enumerate(AUDIENCES):
        for c, diff in enumerate(DIFFICULTIES):
            ax = axes[r][c]
            for i, arm in enumerate(ARM_ORDER):
                ys = []
                for sf in sfs:
                    sel = scored([e for e in episodes if e["arm"] == arm and e["sf"] == sf
                                  and e["audience"] == aud and e["difficulty"] == diff])
                    ys.append(sum(e["score_correct"] for e in sel) / len(sel) if sel else 0.0)
                xs = [j + (i - 1.5) * width for j in range(len(sfs))]
                ax.bar(xs, ys, width=width, color=ARM_COLOR[arm], label=ARM_LABEL[arm],
                       edgecolor="white", linewidth=0.5)
            ax.set_xticks(range(len(sfs)))
            ax.set_xticklabels([f"SF{s}" for s in sfs], fontsize=8)
            ax.set_ylim(0, 1.05)
            ax.set_title(f"{AUD_TITLE[aud].split(' · ')[0].lower()} · {diff}",
                         fontsize=9, loc="left", pad=4)
            ax.tick_params(labelsize=8, length=2.5)
            ax.grid(axis="x", visible=False)
            if c == 0:
                ax.set_ylabel("answers matching gold", fontsize=8.5)

    fig.suptitle("Correctness by audience, difficulty, scale and agent design",
                 fontsize=12.5, weight="bold", x=0.075, ha="left", y=0.965, color="#12151a")
    fig.text(0.075, 0.905,
             "Two questions per cell, three repeats each. A scalar answer counts as correct "
             "only if every value matches; a list only at full recall.",
             fontsize=8, color="#6b7684", ha="left")
    handles = [Line2D([], [], color=ARM_COLOR[a], marker="s", linestyle="none", markersize=7,
                      label=ARM_LABEL[a]) for a in ARM_ORDER]
    fig.legend(handles=handles, loc="lower center", ncol=4, frameon=False, fontsize=9,
               bbox_to_anchor=(0.53, 0.005))
    fig.savefig(out, format="svg")
    plt.close(fig)
    print(f"wrote {out}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--replay", default="outputs/finbench/replay_p99.json")
    p.add_argument("--episodes", default="outputs/finbench/agent_interaction.json")
    p.add_argument("--figures", default="docs/figures")
    args = p.parse_args()

    run = json.loads(Path(args.episodes).read_text())
    figures = Path(args.figures)
    figures.mkdir(parents=True, exist_ok=True)

    replay_path = Path(args.replay)
    if replay_path.exists():
        plot_p99(json.loads(replay_path.read_text())["cells"], run["questions"],
                 figures / "agent-p99-by-question.svg")
    else:
        print(f"skipping the p99 figure: {replay_path} does not exist yet")

    plot_cost(run["episodes"], figures / "agent-cost-by-arm.svg")
    plot_accuracy(run["episodes"], figures / "agent-accuracy-by-cell.svg")


if __name__ == "__main__":
    main()
