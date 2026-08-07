from __future__ import annotations
import importlib.util,sys
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"43_sdcr_development_analysis.py";S=importlib.util.spec_from_file_location("analysis43",P);assert S and S.loader
m=importlib.util.module_from_spec(S);sys.modules[S.name]=m;S.loader.exec_module(m)

def test_schema_failure_scores_zero_in_itt():
    rows=[]
    for arm,score,error in [("left_single",.9,True),("right_single",.2,False),("sdcr_coalition",.4,False)]:rows.append({"candidate_id":"c","arm":arm,"token_f1":score,"response":{"parse_error":error}})
    result=m.analyze({"rows":rows})
    assert result["intention_to_treat"]["mean_token_f1"]["left_single"]==0
    assert result["intention_to_treat"]["coalition_delta"]==.2
