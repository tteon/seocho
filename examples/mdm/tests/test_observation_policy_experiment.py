from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "25_observation_policy_experiment.py"
    spec = importlib.util.spec_from_file_location("observation_policy_experiment", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _records() -> list[dict[str, str]]:
    rows = []
    for category in ("Accounting", "Risk"):
        for case_id in ("c3", "c1", "c2"):
            rows.append(
                {
                    "category": category,
                    "case_id": case_id,
                    "scenario_id": "joint",
                    "model": "m",
                    "error": "",
                    "nodes_created": "999",
                }
            )
    return rows


def test_case_selection_is_balanced_and_metadata_only():
    module = _load_module()
    selected = module.select_cases(_records(), per_category=2)
    assert selected == [
        {"category": "Accounting", "case_id": "c1"},
        {"category": "Accounting", "case_id": "c2"},
        {"category": "Risk", "case_id": "c1"},
        {"category": "Risk", "case_id": "c2"},
    ]


def test_full_gate_has_256_unique_cells():
    module = _load_module()
    cases = [
        {"category": f"category-{index // 2}", "case_id": f"case-{index}"}
        for index in range(16)
    ]
    cells = module.build_cells(cases)
    assert len(cells) == 256
    assert len({cell["cell_id"] for cell in cells}) == 256
    assert {cell["status"] for cell in cells} == {"covered_by_existing_full_factorial"}
