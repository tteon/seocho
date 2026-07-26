from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "46_log2026_paper_figures.py"
SPEC = importlib.util.spec_from_file_location("paper_figures46", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_categorical_palette_slots_are_distinct() -> None:
    """The four categorical slots must be distinct validated hues.

    The previous palette failed the dataviz CVD check (#54A24B vs #F58518 at
    protan delta-E 3.4), so the slots are pinned rather than recomputed.
    """
    slots = (MODULE.BLUE, MODULE.ORANGE, MODULE.AQUA, MODULE.VIOLET)
    assert len(set(slots)) == len(slots)
    assert all(s.startswith("#") and len(s) == 7 for s in slots)


def test_ordinal_ramp_is_monotone_and_single_hue() -> None:
    """The lineage ramp encodes an ordered pipeline, so lightness must decrease."""

    def luminance(hex_color: str) -> float:
        r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    lums = [luminance(c) for c in MODULE.RAMP]
    assert lums == sorted(lums, reverse=True), "ramp must read light to dark"
    assert len(set(MODULE.RAMP)) == len(MODULE.RAMP)


def test_every_figure_reads_from_a_frozen_artifact() -> None:
    """Figures must not hardcode results; each generator loads an artifact."""
    source = PATH.read_text()
    for generator in ("lineage_figure", "category_structure_figure",
                      "divergence_figure", "routing_bottleneck_figure"):
        body = source.split(f"def {generator}(")[1].split("\ndef ")[0]
        assert "load(" in body, f"{generator} must read from an artifact"


def test_decision_figure_names_all_four_actions() -> None:
    """The method figure must show the abstain path, not only the happy paths."""
    body = PATH.read_text().split("def decision_figure(")[1].split("\ndef ")[0]
    for action in ("Single view", "Complementary", "Verification", "Abstain"):
        assert action in body
    # PPR must be shown as a tie-break only, never as part of the gate.
    assert "never open the gate" in body
