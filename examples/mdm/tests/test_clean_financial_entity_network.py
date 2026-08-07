from __future__ import annotations

import importlib.util
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "47_clean_financial_entity_network.py"
SPEC = importlib.util.spec_from_file_location("clean_network", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_single_noisy_ticker_assignment_does_not_create_identity() -> None:
    rows = [
        {"name": "our", "ticker": "STZ", "provider": "a", "category": "Risk"},
        {"name": "our", "ticker": None, "provider": "b", "category": "Accounting"},
    ]
    registry = MODULE.build_identity_registry(rows)
    assert "ticker:STZ" not in registry["accepted"]


def test_independently_supported_ticker_creates_auditable_identity() -> None:
    rows = [
        {"name": "honeywell international", "ticker": "HON", "provider": "a", "category": "Risk"},
        {"name": "honeywell international", "ticker": "HON", "provider": "b", "category": "Risk"},
    ]
    registry = MODULE.build_identity_registry(rows)
    assert registry["accepted"]["ticker:HON"]["aliases"] == ["honeywell international"]


def test_conflicting_tickers_quarantine_alias() -> None:
    rows = [
        {"name": "ambiguous bank", "ticker": "AAA", "provider": "a", "category": "Risk"},
        {"name": "ambiguous bank", "ticker": "BBB", "provider": "b", "category": "Risk"},
    ]
    registry = MODULE.build_identity_registry(rows)
    assert not registry["accepted"]
    assert registry["conflicts"][0]["decision"] == "quarantine"
