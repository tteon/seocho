from __future__ import annotations

import importlib.util
from pathlib import Path


def load(name: str, filename: str):
    path = Path(__file__).resolve().parents[1] / filename
    spec = importlib.util.spec_from_file_location(name, path); assert spec and spec.loader
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


SUITE = load("mixed_suite", "50_mixed_sdcr_suite.py")
SELECTOR = load("selector_eval", "51_sdcr_selector_eval.py")


def test_mixed_suite_contains_all_pre_registered_classes() -> None:
    payload = SUITE.build()
    assert payload["counts"] == {"complementary": 28, "conflict": 8, "local": 28, "protected": 8, "unanswerable": 8}
    assert not payload["selection_uses_model_outputs"]


def test_conflict_forces_verification_and_denial_forces_abstention() -> None:
    descriptors = {"Risk": {"risk": 1.0}, "Legal": {"legal": 1.0}}
    conflict = {"query_id": "q1", "query_class": "conflict", "question": "risk", "component_case_ids": ["c1"],
                "expected_action": "verification_coalition", "intervention": {"conflict_detected": True}}
    receipt = SELECTOR.route(conflict, descriptors, {"risk": 1.0}, {}, "sdcr")
    assert receipt["action"] == "verification_coalition"
    denied = {"query_id": "q2", "query_class": "unanswerable", "question": "risk", "component_case_ids": ["c2"],
              "expected_action": "abstain", "intervention": {"deny_categories": ["Risk"]}}
    receipt = SELECTOR.route(denied, descriptors, {"risk": 1.0}, {}, "sdcr")
    assert receipt["action"] == "abstain"


def test_network_is_disabled_for_no_network_ablation() -> None:
    descriptors = {"A": {"alpha": 1.0}, "B": {"beta": 1.0}}
    frame = {"query_id": "q", "query_class": "local", "question": "alpha", "component_case_ids": ["c"],
             "expected_action": "single"}
    receipt = SELECTOR.route(frame, descriptors, {"alpha": 1.0}, {("A", "B"): 1.0}, "sdcr_no_network")
    assert receipt["network_role"] == "disabled"
