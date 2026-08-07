from __future__ import annotations

import importlib.util
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "70_capability_routing_baselines.py"
SPEC = importlib.util.spec_from_file_location("routing_baselines", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Exact:
    @staticmethod
    def alternate(left, right):
        return [*left, *right]

    @staticmethod
    def cap(nodes, budget, encoder):
        return nodes, len(nodes)

    @staticmethod
    def coverage(nodes, gold):
        return (1.0 if nodes and nodes[0]["id"] == "left" else 0.0, None)


def test_selector_order_is_preserved_under_serialization() -> None:
    item = {
        "required_categories": ["left", "right"],
        "arms": {
            "left_single": {"evidence": [{"id": "left"}]},
            "right_single": {"evidence": [{"id": "right"}]},
        },
        "golds": ["a", "b", "c"],
    }
    forward = MODULE.score_selected(item, ("left", "right"), Exact(), None)
    reverse = MODULE.score_selected(item, ("right", "left"), Exact(), None)
    assert forward["tokens_used"] == reverse["tokens_used"] == 2
    assert forward["slot_token_recall"] > reverse["slot_token_recall"]
