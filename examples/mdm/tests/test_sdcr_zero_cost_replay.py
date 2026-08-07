from __future__ import annotations
import importlib.util,sys
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"32_sdcr_zero_cost_replay.py";S=importlib.util.spec_from_file_location("replay",P);assert S and S.loader
m=importlib.util.module_from_spec(S);sys.modules[S.name]=m;S.loader.exec_module(m)

def test_mutation_changes_numeric_value():
    assert m.mutate_value("$100 million")!="$100 million"

def test_denied_field_removed_recursively():
    assert m.remove_denied({"ok":1,"secret":"x","nested":{"secret":"y"}},{"secret"})=={"ok":1,"nested":{}}

def test_structured_conflict_requires_comparability():
    frame={"query_id":"q","intervention":{"target_provider":"silo-p","synthetic_marker":"P"}}
    record={"survivorship":{"golden":[{"metric":"Revenue","period":"fy2023","basis":"reported","value":"$100","source":"p"}]}}
    row=m.replay_verification(frame,record);assert row["eligible"] and row["comparable"] and row["conflict_detected"]
