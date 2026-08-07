from __future__ import annotations

import importlib.util
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "71_full_finder_verification_pool.py"
SPEC = importlib.util.spec_from_file_location("verification_pool", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_selection_is_balanced_by_category_and_never_reuses_a_case() -> None:
    facts = [
        {"case_id": "a1", "category": "Accounting", "metric": "Revenue", "period": "2023", "value": "$10", "currency": "USD", "basis": "structured_MonetaryAmount", "source_provider": "deepseek", "target_provider": "gptoss", "source_workspace": "w1", "source_node_id": "n1", "labels": ["MonetaryAmount"]},
        {"case_id": "a1", "category": "Accounting", "metric": "Cost", "period": "2023", "value": "$8", "currency": "USD", "basis": "structured_MonetaryAmount", "source_provider": "deepseek", "target_provider": "gptoss", "source_workspace": "w1", "source_node_id": "n2", "labels": ["MonetaryAmount"]},
        {"case_id": "a2", "category": "Accounting", "metric": "Cash Flow", "period": "2023", "value": "$2", "currency": "USD", "basis": "structured_CashFlow", "source_provider": "deepseek", "target_provider": "gptoss", "source_workspace": "w2", "source_node_id": "n3", "labels": ["CashFlow"]},
        {"case_id": "f1", "category": "Financials", "metric": "Revenue", "period": "2023", "value": "$4", "currency": "USD", "basis": "structured_MonetaryAmount", "source_provider": "deepseek", "target_provider": "gptoss", "source_workspace": "w3", "source_node_id": "n4", "labels": ["MonetaryAmount"]},
    ]
    selected, _ = MODULE.select_balanced(facts, per_category=2)
    assert len(selected) == 3
    assert len({row["case_id"] for row in selected}) == len(selected)
    assert all(row["comparable"] and row["conflict_detected"] for row in selected)
    assert all(row["original_fact"]["value"] != row["poisoned_fact"]["value"] for row in selected)


def test_value_validation_rejects_non_numeric_values() -> None:
    assert MODULE.valid_amount("$12.50") == "$12.50"
    assert MODULE.valid_amount("not reported") is None
    assert MODULE.valid_amount("about twelve") is None
