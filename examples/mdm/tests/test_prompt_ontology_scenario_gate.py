from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_runner():
    path = Path(__file__).resolve().parents[1] / "18_prompt_ontology_scenario_gate.py"
    spec = importlib.util.spec_from_file_location("prompt_ontology_scenario_gate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_gate_promotes_fact_gain_without_entity_collapse():
    runner = _load_runner()

    gate = runner._gate(
        {
            "entities": 100,
            "facts": 12,
            "generic_entity_ratio": 0.20,
            "cross_provider_clusters": 4,
        },
        {
            "entities": 100,
            "facts": 10,
            "generic_entity_ratio": 0.20,
            "cross_provider_clusters": 4,
        },
    )

    assert gate["promote_to_full_reindex"] is True
    assert gate["fact_gain"] == 2


def test_gate_rejects_entity_collapse_even_with_more_facts():
    runner = _load_runner()

    gate = runner._gate(
        {
            "entities": 40,
            "facts": 20,
            "generic_entity_ratio": 0.05,
            "cross_provider_clusters": 4,
        },
        {
            "entities": 100,
            "facts": 10,
            "generic_entity_ratio": 0.20,
            "cross_provider_clusters": 4,
        },
    )

    assert gate["promote_to_full_reindex"] is False
    assert gate["entity_collapse_guard"] is True


def test_scenario_workspace_is_isolated_from_baseline_workspace():
    runner = _load_runner()

    assert runner._scenario_workspace("a-b", "minimax25", "case1") == (
        "fedcat-scenario-a-b-minimax25-case1"
    )
