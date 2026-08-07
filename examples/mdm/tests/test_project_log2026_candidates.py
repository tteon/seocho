from __future__ import annotations
import importlib.util,sys
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"40_project_log2026_candidates.py";S=importlib.util.spec_from_file_location("projection40",P);assert S and S.loader
m=importlib.util.module_from_spec(S);sys.modules[S.name]=m;S.loader.exec_module(m)

def test_workspace_selects_only_survivorship_scenario():
    assert m.workspace("minimax27","abc")=="fedcat-scenario-duplicate-aware-survivorship-v1-fibo-finance-core-minimax27-abc"
