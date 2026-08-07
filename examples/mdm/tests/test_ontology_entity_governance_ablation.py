from __future__ import annotations

import importlib.util
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "49_ontology_entity_governance_ablation.py"
SPEC = importlib.util.spec_from_file_location("ontology_ablation", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_governance_quarantines_ambiguous_regulator_type(tmp_path: Path) -> None:
    clean = {
        "identity_registry": {"accepted": {"ticker:AAA": {"aliases": ["alpha"], "providers": ["a", "b"], "categories": ["Risk"]}}},
        "entity_audit": [
            {"entity": "alpha inc", "included": True, "labels": ["LegalEntity", "Regulator"],
             "categories": ["Risk", "Legal"], "observations": 4},
        ],
    }
    ontology = MODULE.build_ontology(tmp_path / "governance.owl")
    selected, receipts = MODULE.govern(clean, ontology)
    assert selected == []
    assert receipts[0]["decision"] == "quarantine"


def test_governance_accepts_supported_legal_entity(tmp_path: Path) -> None:
    clean = {
        "identity_registry": {"accepted": {"ticker:AAA": {"aliases": ["alpha"], "providers": ["a", "b"], "categories": ["Risk"]}}},
        "entity_audit": [
            {"entity": "alpha corporation", "included": True, "labels": ["LegalEntity"],
             "categories": ["Risk", "Legal"], "observations": 4},
        ],
    }
    ontology = MODULE.build_ontology(tmp_path / "governance.owl")
    selected, receipts = MODULE.govern(clean, ontology)
    assert selected == ["alpha corporation"]
    assert receipts[0]["decision"] == "accept"
