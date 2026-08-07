from __future__ import annotations
import importlib.util
import sys
from pathlib import Path

P=Path(__file__).resolve().parents[1]/"36_cross_view_adjudication_gate.py"
S=importlib.util.spec_from_file_location("gate",P);assert S and S.loader
m=importlib.util.module_from_spec(S);sys.modules[S.name]=m;S.loader.exec_module(m)

def review(decision: str="accept") -> dict[str,str]:
    return {"both_views_required":"yes","single_view_sufficient":"no","financially_natural":"yes","gold_slots_valid":"yes","decision":decision,"rationale":"Independent source inspection supports the label."}

def test_null_reviews_never_unlock_evaluation():
    candidates={"candidates":[{"candidate_id":"c1"}]}
    annotations={"annotations":[{"candidate_id":"c1","reviewer_1":None,"reviewer_2":None,"adjudicated":None}]}
    result=m.freeze(candidates,annotations)
    assert result["accepted_count"]==0
    assert result["evaluation_unlocked"] is False

def test_two_reviews_and_final_accept_unlock():
    candidates={"candidates":[{"candidate_id":"c1"}]}
    annotations={"annotations":[{"candidate_id":"c1","reviewer_1":review(),"reviewer_2":review(),"adjudicated":review()}]}
    result=m.freeze(candidates,annotations)
    assert result["complete_count"]==1
    assert result["accepted_count"]==1
