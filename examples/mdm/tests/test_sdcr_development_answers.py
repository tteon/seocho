from __future__ import annotations
import importlib.util,sys
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"42_sdcr_development_answers.py";S=importlib.util.spec_from_file_location("answers42",P);assert S and S.loader
m=importlib.util.module_from_spec(S);sys.modules[S.name]=m;S.loader.exec_module(m)

def test_token_f1_identity_and_number_recall():
    assert m.token_f1("Revenue $100","Revenue $100")==1
    assert m.number_recall("It was $100","Gold $100 and $200")==.5
