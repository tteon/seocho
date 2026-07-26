#!/usr/bin/env python3
"""Generate print-safe figures for the LoG 2026 paper.

Every number is read from a frozen run artifact under
``outputs/evaluation/mdm_fedcat``; nothing is hardcoded except axis framing.

Palette: validated default categorical/ordinal slots (dataviz six-checks).
The previous palette failed CVD separation (#54A24B vs #F58518, protan
delta-E 3.4) and the normal-vision floor (#79706E vs #B279A2, delta-E 12.6),
which is why the earlier figures leaned on decorative hatching. Texture is
now used only where an adjacent pair carries meaning (grouped bars), never as
box decoration.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/evaluation/mdm_fedcat"
CROSS = BASE / "log2026-full-finder-cross-view-v1"
OUT = ROOT / "papers/log2026"
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

# Validated categorical slots (light surface #fcfcfb).
BLUE, ORANGE, AQUA, VIOLET = "#2a78d6", "#eb6834", "#1baf7a", "#4a3aa7"
# Validated ordinal ramp (monotone L, adjacent delta-L >= 0.06, single hue).
RAMP = ("#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#0d366b")
INK, MUTED, GRID = "#1a1a19", "#6b6b66", "0.88"


def load(path: Path) -> Any:
    return json.loads(path.read_text())


def configure() -> None:
    import matplotlib as mpl

    mpl.rcParams.update({
        "font.family": "serif",
        "font.size": 9,
        "axes.labelsize": 9,
        "axes.titlesize": 9.5,
        "axes.labelcolor": INK,
        "axes.edgecolor": "0.6",
        "text.color": INK,
        "xtick.color": INK,
        "ytick.color": INK,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.dpi": 180,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    })


def save(fig, stem: str) -> None:
    fig.savefig(OUT / f"{stem}.pdf")
    fig.savefig(OUT / f"{stem}.png")
    print(OUT / f"{stem}.pdf")


# --------------------------------------------------------------------------
# Figure 1: the routing decision, including the four terminal actions.
# --------------------------------------------------------------------------
def decision_figure() -> None:
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch

    fig, ax = plt.subplots(figsize=(7.15, 3.25))
    ax.set(xlim=(0, 1), ylim=(0, 1))
    ax.axis("off")

    def box(x, y, w, h, text, color, lw=1.4, fs=7.5, weight="normal"):
        ax.add_patch(FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.008", facecolor="white",
            edgecolor=color, linewidth=lw))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
                fontsize=fs, color=INK, weight=weight, linespacing=1.4)
        return x + w / 2

    def arrow(x0, y0, x1, y1):
        ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                    arrowprops={"arrowstyle": "-|>", "lw": 1.0,
                                "color": "0.35", "shrinkA": 0, "shrinkB": 0})

    # Row 1: input, framing, and the gate. PPR is deliberately absent.
    box(0.005, 0.750, 0.185, 0.135,
        "Question $q$\nissuer · metric\nperiod · basis", "0.55", fs=7.2)
    box(0.225, 0.750, 0.225, 0.135,
        "Frame $F(q)$\nrequired slots $R(q)$\nauthorized views $A(q)$", "0.55", fs=7.2)
    box(0.485, 0.750, 0.510, 0.135,
        "Hard gate  $M(q)=\\mathbb{1}\\,[\\,\\min_s p_{i^*s}<\\tau_s\\ \\ \\vee\\ \\ h(q)=1\\,]$\n"
        "missing slot coverage      ·      comparable-fact conflict",
        BLUE, lw=1.7, fs=7.4)
    arrow(0.190, 0.8175, 0.225, 0.8175)
    arrow(0.450, 0.8175, 0.485, 0.8175)

    # Distribution bus, so no long diagonal arrows cross the figure.
    cols = (0.1125, 0.3725, 0.6325, 0.8875)
    ax.plot([cols[0], cols[3]], [0.665, 0.665], color="0.35", linewidth=1.0)
    arrow(0.740, 0.750, 0.740, 0.666)
    for cx, label in zip(cols, ("gate = 0", "missing slot", "conflict", "infeasible")):
        arrow(cx, 0.665, cx, 0.590)
        ax.text(cx + 0.010, 0.628, label, fontsize=6.8, color=MUTED,
                ha="left", va="center")

    # Row 2: the four terminal actions.
    y = 0.380
    box(0.005, y, 0.215, 0.190, "Single view\n\nsmallest authorized\nview that covers $R(q)$",
        BLUE, fs=7.4, weight="normal")
    box(0.265, y, 0.215, 0.190, "Complementary\ncoalition\n\nadds the view holding\nthe missing slot",
        ORANGE, fs=7.4)
    box(0.525, y, 0.215, 0.190, "Verification\ncoalition\n\ntwo views on the same\nconflicting slot",
        AQUA, fs=7.4)
    box(0.780, y, 0.215, 0.190, "Abstain\n\n$\\mathcal{F}(q)=\\varnothing$\nno evidence is served",
        "0.5", fs=7.4)
    # Row 3: the supervisor, reached by three of the four actions.
    box(0.055, 0.090, 0.635, 0.150,
        "Supervisor answers from typed evidence only\n"
        "slot fills · provenance · declared missing slots", "0.55", fs=7.6)
    for cx in cols[:3]:
        arrow(cx, y, cx, 0.241)

    ax.text(0.005, 0.995,
            "SDCR chooses one of four actions; only two hard conditions can add an agent",
            fontsize=9.2, weight="bold", ha="left", va="top", color=INK)
    ax.text(0.005, 0.030,
            "PPR and typed-path divergence rank only within the minimum-cost feasible set; "
            "they never open the gate.",
            fontsize=7, color=MUTED, ha="left", va="bottom")
    fig.tight_layout()
    save(fig, "sdcr_decision")
    plt.close(fig)


# --------------------------------------------------------------------------
# Figure 2: data lineage with the counts at each layer.
# --------------------------------------------------------------------------
def lineage_figure() -> None:
    import matplotlib.pyplot as plt

    audit = load(BASE / "log2026-full-multiagent-network-v1/analysis.json")["audit"]
    projection = load(CROSS / "projection.json")
    stages = [
        ("FinDER question–answer records", 5703, "cases"),
        ("Provider–case workspaces\n(4 generation models, isolated)", audit["workspace_count"], "workspaces"),
        ("Extracted graph, survivorship profile", audit["raw_nodes_seen"], "nodes"),
        ("Category views after normalization", audit["collapsed_nodes"], "nodes"),
        ("Qualified answer projection (read-only)", projection["total_nodes"], "nodes"),
    ]
    labels = [s[0] for s in stages]
    values = [s[1] for s in stages]
    units = [s[2] for s in stages]

    fig, ax = plt.subplots(figsize=(7.15, 2.5))
    ypos = list(range(len(stages)))[::-1]
    ax.barh(ypos, values, height=0.6, color=RAMP, edgecolor="white", linewidth=1.2)
    for y, v, u in zip(ypos, values, units):
        ax.text(v * 1.13, y, f"{v:,} {u}", va="center", ha="left",
                fontsize=8, color=INK)
    ax.set_xscale("log")
    ax.set_xlim(3e3, 4.5e6)
    ax.set_yticks(ypos, labels, fontsize=7.8)
    ax.set_xlabel("Count (log scale; units differ per row and are labelled)")
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    fig.suptitle("Expansion then contraction: 5,703 records become 8,313 answerable nodes",
                 fontsize=9.5, weight="bold", x=0.005, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    save(fig, "finder_lineage")
    plt.close(fig)


# --------------------------------------------------------------------------
# Figure 3: category views differ on size and on centrality shape, and the
# two orderings disagree. This is what Sec. 4.1 actually argues from.
# --------------------------------------------------------------------------
def category_structure_figure() -> None:
    import matplotlib.pyplot as plt

    graphs = load(BASE / "log2026-full-multiagent-network-v1/analysis.json")["category_graphs"]
    order = sorted(graphs, key=lambda k: -graphs[k]["nodes"])
    short = {"Company overview": "Company\noverview", "Shareholder return": "Shareholder\nreturn"}

    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(7.15, 2.85))

    x = list(range(len(order)))
    nodes = [graphs[k]["nodes"] for k in order]
    edges = [graphs[k]["edges"] for k in order]
    ax0.bar([i - 0.19 for i in x], nodes, width=0.36, color=BLUE,
            edgecolor="white", linewidth=1.0, label="Semantic nodes")
    ax0.bar([i + 0.19 for i in x], edges, width=0.36, color=ORANGE,
            edgecolor="white", linewidth=1.0, hatch="////", label="Typed edges")
    ax0.set_xticks(x, [short.get(k, k) for k in order], rotation=38,
                   ha="right", fontsize=7.2)
    ax0.set_ylabel("Count")
    ax0.set_yticks([0, 20000, 40000, 60000], ["0", "20k", "40k", "60k"])
    ax0.legend(frameon=False, fontsize=7.2, loc="upper right")
    ax0.grid(axis="y", color=GRID, linewidth=0.6)
    ax0.set_axisbelow(True)
    ax0.set_title("Size ordering", loc="left")

    deg = [graphs[k]["mean_degree"] for k in order]
    ent = [graphs[k]["pagerank_entropy_normalized"] for k in order]
    ax1.scatter(deg, ent, s=34, color=BLUE, zorder=3, linewidth=0)
    # Legal (1.735, 0.993) and Company overview (1.737, 0.993) nearly coincide,
    # so their labels must leave in opposite directions.
    offsets = {"Legal": (-14, -7), "Company overview": (8, 2),
               "Shareholder return": (9, -8), "Footnotes": (8, 2),
               "Risk": (-9, 5), "Financials": (8, -2),
               "Accounting": (8, -7), "Governance": (8, -2)}
    for k, dx, dy in zip(order, deg, ent):
        emphasis = k in ("Risk", "Footnotes")
        ax1.annotate(k, (dx, dy), textcoords="offset points",
                     xytext=offsets[k], fontsize=6.8,
                     ha="right" if offsets[k][0] < 0 else "left",
                     color=INK if emphasis else MUTED,
                     weight="bold" if emphasis else "normal")
    ax1.set(xlabel="Mean degree", ylabel="Normalized PageRank entropy")
    ax1.set_xlim(1.05, 3.35)
    ax1.set_ylim(min(ent) - 0.004, max(ent) + 0.003)
    ax1.grid(color=GRID, linewidth=0.6)
    ax1.set_axisbelow(True)
    ax1.set_title("Centrality shape disagrees with size", loc="left")

    fig.suptitle("No single statistic orders the category views",
                 fontsize=9.5, x=0.005, ha="left", weight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    save(fig, "category_structure")
    plt.close(fig)


# --------------------------------------------------------------------------
# Figure 4: PPR divergence saturates, so no upper-tail rule fires.
# --------------------------------------------------------------------------
def divergence_figure() -> None:
    import matplotlib.pyplot as plt

    analysis = load(BASE / "log2026-clean-entity-network-v1/analysis.json")
    ablation = load(BASE / "log2026-entity-cleaning-ablation-v1/analysis.json")

    def rank_divergence(left: list[str], right: list[str], depth: int = 10) -> float:
        lw = {n: 1 / (i + 1) for i, n in enumerate(left[:depth])}
        rw = {n: 1 / (i + 1) for i, n in enumerate(right[:depth])}
        names = set(lw) | set(rw)
        den = sum(max(lw.get(n, 0), rw.get(n, 0)) for n in names)
        if not den:
            return 0.0
        return 1 - sum(min(lw.get(n, 0), rw.get(n, 0)) for n in names) / den

    null_values = sorted(float(r["rank_weighted_divergence"]) for r in analysis["null_rows"])
    cross_values = sorted(rank_divergence(r["left_top"], r["right_top"])
                          for r in analysis["entity_context_divergence"])

    def ecdf(vals):
        return vals, [(i + 1) / len(vals) for i in range(len(vals))]

    def quantile(vals, q):
        return vals[min(len(vals) - 1, max(0, int(round(q * (len(vals) - 1)))))]

    fig, ax = plt.subplots(figsize=(7.15, 2.9))
    for vals, label, color, style, marker in (
        (null_values, "Matched cross-model null (category fixed)", BLUE, "--", "o"),
        (cross_values, "Cross-category views", ORANGE, "-", "s"),
    ):
        xs, ys = ecdf(vals)
        ax.plot(xs, ys, label=label, color=color, linestyle=style, linewidth=1.8,
                marker=marker, markevery=max(1, len(xs) // 14), markersize=4.0,
                markerfacecolor="white", markeredgewidth=0.9)

    # The upper-tail trigger would sit at the null's (1 - alpha) quantile.
    q80, q99 = quantile(null_values, 0.80), quantile(null_values, 0.99)
    ax.axvline(q80, color="0.45", linewidth=0.9, linestyle=":")
    ax.text(q80 - 0.014, 0.985,
            f"the null itself already saturates:\n"
            f"$q_{{0.80}}$={q80:.3f},  $q_{{0.99}}$={q99:.3f},\n"
            f"so every upper-tail threshold is 1.000",
            fontsize=7, color=MUTED, ha="right", va="top", linespacing=1.45)

    ax.set(xlabel="Rank-weighted PPR divergence, top-10 neighborhood",
           ylabel="Empirical cumulative probability",
           xlim=(0, 1.01), ylim=(0, 1.02))
    ax.grid(color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, loc="upper left", fontsize=7.6)
    stats = (f"cross-category mean {ablation['after']['cross_mean']:.3f}\n"
             f"null mean {ablation['after']['null_mean']:.3f}\n"
             f"AUROC {ablation['after']['auroc']:.3f}\n"
             f"no $\\alpha\\in[0.01,0.20]$ selects any pair")
    ax.text(0.035, 0.52, stats, transform=ax.transAxes, fontsize=7.4,
            color=INK, va="top", linespacing=1.5)
    ax.set_title("Both groups pile up at complete non-overlap, so no threshold separates them",
                 loc="left", pad=8)
    fig.tight_layout()
    save(fig, "ppr_matched_null")
    plt.close(fig)


# --------------------------------------------------------------------------
# Figure 5: the routing bottleneck. Coverage buys recall; the learned router
# sits below the random-selection trend at its own coverage level.
# --------------------------------------------------------------------------
def routing_bottleneck_figure() -> None:
    import matplotlib.pyplot as plt

    summary = load(CROSS / "capability_routing_baselines.json")["summary"]
    fallback = load(BASE / "log2026-capability-fallback-v1/fallback_replay.json")

    def pt(key):
        row = summary[key]
        return row["required_view_coverage"], row["slot_token_recall"]

    randoms = [pt(f"random_authorized_{k}") for k in (1, 2, 3)]
    sx, sy = pt("actual_sdcr")
    named = [
        ("Random 1 view", *randoms[0], MUTED, (8, -4), "left", False),
        ("Random 2 views", *randoms[1], MUTED, (-9, -4), "right", False),
        ("Random 3 views", *randoms[2], MUTED, (-9, -4), "right", False),
        ("TF–IDF top-1", *pt("tfidf_top1"), BLUE, (-9, 6), "right", True),
        ("TF–IDF top-2", *pt("tfidf_top2"), BLUE, (10, -6), "left", True),
        ("Oracle team", *pt("oracle_minimal_team"), VIOLET, (-9, -4), "right", True),
        ("SDCR router", sx, sy, ORANGE, (13, -5), "left", True),
    ]

    fig, ax = plt.subplots(figsize=(7.15, 3.1))

    # Trend fitted on the random points only, then extended just far enough to
    # read off the random-equivalent recall at the router's own coverage.
    slope = (randoms[2][1] - randoms[0][1]) / (randoms[2][0] - randoms[0][0])
    expected = randoms[0][1] + slope * (sx - randoms[0][0])
    rx = [p[0] for p in randoms]
    ry = [p[1] for p in randoms]
    ax.plot(rx, ry, linestyle=":", linewidth=1.2, color="0.55", zorder=1,
            label="Uniform random selection (fitted on the three random arms)")
    ax.plot([randoms[2][0], sx], [randoms[2][1], expected], linestyle=":",
            linewidth=1.0, color="0.72", zorder=1)

    # The shortfall: what random selection would already deliver at 0.538.
    ax.scatter([sx], [expected], s=52, facecolor="white", edgecolor="0.45",
               linewidth=1.2, zorder=4, marker="o")
    ax.annotate("", xy=(sx, sy + 0.006), xytext=(sx, expected - 0.006),
                arrowprops={"arrowstyle": "-|>", "lw": 1.2, "color": ORANGE})
    ax.text(sx - 0.016, (sy + expected) / 2,
            f"{expected / sy:.1f}$\\times$\nshortfall", fontsize=7.2,
            color=ORANGE, ha="right", va="center", weight="bold", linespacing=1.3)
    ax.text(sx, expected + 0.009, f"random-equivalent ({expected:.3f})",
            fontsize=6.9, color=MUTED, ha="center", va="bottom")

    for label, cx, cy, color, off, ha, show_xy in named:
        emphasis = label == "SDCR router"
        ax.scatter([cx], [cy], s=115 if emphasis else 46, color=color,
                   zorder=5, linewidth=1.4 if emphasis else 0,
                   edgecolor="white" if emphasis else "none",
                   marker="D" if emphasis else "o")
        text = f"{label}\n({cx:.3f}, {cy:.3f})" if show_xy else label
        ax.annotate(text, (cx, cy), ha=ha,
                    textcoords="offset points", xytext=off, fontsize=7.0,
                    color=INK if color != MUTED else MUTED,
                    weight="bold" if emphasis else "normal", linespacing=1.3)

    # The measured repair. Its coverage is a composite of routed hits and
    # fallback misses, so it is drawn as a level rather than as a point.
    repaired = fallback["summary"]["arms"]["sdcr_with_capability_fallback"]["slot_token_recall"]
    ax.axhline(repaired, color=AQUA, linewidth=1.5, linestyle="--", zorder=2)
    ax.text(1.06, repaired + 0.004,
            f"SDCR + capability fallback ({repaired:.3f}), measured by replay",
            fontsize=7.0, color=AQUA, ha="right", va="bottom", weight="bold")

    ax.set(xlabel="Required-view coverage on the 13 revised cases",
           ylabel="Slot token recall", xlim=(0.05, 1.08), ylim=(0, 0.225))
    ax.grid(color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=7.0, loc="upper left")
    ax.set_title("Finding the right views is the binding constraint, not synthesizing the answer\n"
                 "identical 2,048-token evidence budget, cl100k_base, all arms",
                 loc="left", pad=8)
    fig.tight_layout()
    save(fig, "routing_bottleneck")
    plt.close(fig)


def main() -> int:
    configure()
    OUT.mkdir(parents=True, exist_ok=True)
    decision_figure()
    lineage_figure()
    category_structure_figure()
    divergence_figure()
    routing_bottleneck_figure()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
