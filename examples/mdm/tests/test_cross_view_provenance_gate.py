from __future__ import annotations
import importlib.util,sys
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"38_cross_view_provenance_gate.py";S=importlib.util.spec_from_file_location("gate38",P);assert S and S.loader
m=importlib.util.module_from_spec(S);sys.modules[S.name]=m;S.loader.exec_module(m)

def test_number_normalization_handles_currency_commas_and_percent():
    assert m.numbers("$1,200 and 10.0%") == {"1200","10"}

def test_provenance_pass_requires_both_slots_and_rejects_opposite_leakage():
    good=m.slot_metrics("Revenue was $100 in 2024",["Revenue was $100 in 2024"],["Legal risk only"])
    assert m.qualifies([good,good])
    leaked=m.slot_metrics("Revenue was $100 in 2024",["Revenue was $100 in 2024"],["Revenue was $100 in 2024"])
    assert not m.qualifies([good,leaked])
