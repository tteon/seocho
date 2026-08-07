from __future__ import annotations
import importlib.util,sys
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"45_author_adjudicate_heldout.py";S=importlib.util.spec_from_file_location("author45",P);assert S and S.loader
m=importlib.util.module_from_spec(S);sys.modules[S.name]=m;S.loader.exec_module(m)

def test_known_cross_issuer_contamination_is_rejected():
    assert "pg-60fefd59-79582bd4" in m.REJECT
    assert "issuer contamination" in m.REJECT["pg-60fefd59-79582bd4"]
