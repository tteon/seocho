from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_runner():
    path = Path(__file__).resolve().parents[1] / "17_category_federation_agent.py"
    spec = importlib.util.spec_from_file_location("category_federation_agent", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _policy(mode: str, primary: str = "minimax25") -> dict:
    return {
        "routing_mode": mode,
        "primary_provider": primary,
        "provider_scores": {
            "deepseek": 0.1,
            "gptoss": 0.05,
            "minimax25": 0.4,
            "minimax27": 0.3,
        },
    }


def test_best_provider_then_fact_union_selects_primary_and_fact_providers():
    runner = _load_runner()
    selected = runner.select_providers_for_policy(
        _policy("category_db_best_provider_then_fact_union", primary="minimax25"),
        {
            "deepseek": {"nodes": 10, "facts": 0},
            "gptoss": {"nodes": 10, "facts": 2},
            "minimax25": {"nodes": 10, "facts": 1},
            "minimax27": {"nodes": 10, "facts": 3},
        },
    )

    assert selected == ["minimax25", "minimax27", "gptoss"]


def test_survivorship_first_prefers_fact_providers_over_entity_only_providers():
    runner = _load_runner()
    selected = runner.select_providers_for_policy(
        _policy("category_db_survivorship_first", primary="minimax25"),
        {
            "deepseek": {"nodes": 8, "facts": 0},
            "gptoss": {"nodes": 3, "facts": 0},
            "minimax25": {"nodes": 9, "facts": 2},
            "minimax27": {"nodes": 9, "facts": 1},
        },
    )

    assert selected == ["minimax25", "minimax27"]


def test_multi_provider_context_orders_available_by_score_then_coverage():
    runner = _load_runner()
    selected = runner.select_providers_for_policy(
        _policy("category_db_multi_provider_context", primary="minimax25"),
        {
            "deepseek": {"nodes": 8, "facts": 0},
            "gptoss": {"nodes": 0, "facts": 0},
            "minimax25": {"nodes": 9, "facts": 0},
            "minimax27": {"nodes": 9, "facts": 0},
        },
    )

    assert selected == ["minimax25", "minimax27", "deepseek"]
