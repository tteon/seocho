from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "examples/mdm" / file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_random_team_coverage_is_bounded() -> None:
    baseline = load("capability", "70_capability_routing_baselines.py")
    item = {
        "required_categories": ["Risk", "Legal"],
        "arms": {
            "left_single": {"evidence": []},
            "right_single": {"evidence": []},
        },
        "golds": ["a", "b", "a b"],
    }

    class Exact:
        @staticmethod
        def cap(order, budget, encoder): return order, 0
        @staticmethod
        def coverage(evidence, gold): return (0.0, 0.0)
        @staticmethod
        def alternate(left, right): return left + right

    score = baseline.score_selected(item, ("Risk",), Exact(), None)
    assert score["required_view_coverage"] == 0.5
    assert score["slot_token_recall"] == 0.0
