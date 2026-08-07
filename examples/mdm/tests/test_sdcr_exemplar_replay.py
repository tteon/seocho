from __future__ import annotations
import importlib.util,sys
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"31_sdcr_exemplar_replay.py";S=importlib.util.spec_from_file_location("ex",P);assert S and S.loader
m=importlib.util.module_from_spec(S);sys.modules[S.name]=m;S.loader.exec_module(m)

def test_all_exemplars_match_declared_policy():
    assert all(m.replay(case)["matches_expected"] for case in m.CASES)

def test_unanswerable_preserves_missing_slots():
    row=m.replay(m.CASES[-1]);assert row["mode"]=="abstain";assert "constant_currency_basis" in row["missing_slots"]
