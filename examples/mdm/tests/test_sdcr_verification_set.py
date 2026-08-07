from __future__ import annotations
import importlib.util,sys
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"33_sdcr_verification_set.py";S=importlib.util.spec_from_file_location("verify",P);assert S and S.loader
m=importlib.util.module_from_spec(S);sys.modules[S.name]=m;S.loader.exec_module(m)

def test_selection_rejects_missing_structured_fact():
    accepted,rejected=m.build([{"case_id":"a","category":"Risk","survivorship":None}]);assert not accepted and rejected[0]["reason"]=="no_metric_period_value_fact"

def test_selection_creates_comparable_conflict():
    record={"case_id":"a","category":"Financials","effective_selected_providers":["p1","p2"],"survivorship":{"golden":[{"metric":"Revenue","period":"fy2023","basis":"reported","value":"$100","source":"p1"}]}}
    accepted,rejected=m.build([record]);assert not rejected and accepted[0]["comparable"] and accepted[0]["conflict_detected"] and accepted[0]["target_provider"]=="p2"

def test_selection_rejects_missing_comparison_basis():
    record={"case_id":"a","category":"Financials","effective_selected_providers":["p1","p2"],"survivorship":{"golden":[{"metric":"Revenue","period":"fy2023","basis":"","value":"$100","source":"p1"}]}}
    accepted,rejected=m.build([record]);assert not accepted and rejected[0]["reason"]=="comparison_basis_missing"
