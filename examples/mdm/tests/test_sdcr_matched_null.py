from __future__ import annotations
import importlib.util,sys
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"29_sdcr_matched_null.py";S=importlib.util.spec_from_file_location("null",P);assert S and S.loader
m=importlib.util.module_from_spec(S);sys.modules[S.name]=m;S.loader.exec_module(m)

def test_divergence_bounds():
    assert m.jaccard_divergence(["a"],["a"])==0
    assert m.jaccard_divergence(["a"],["b"])==1

def test_auc_separates_ordered_samples():
    assert m.auc([0,.1],[.9,1])==1
    assert m.auc([.5],[.5])==.5

def test_tail_has_finite_sample_correction():
    assert m.tail_pvalue(.9,[.1,.2,.3])==.25

def test_rank_weighted_divergence_respects_order():
    assert m.rank_weighted_divergence(["a","b"],["a","b"]) == 0
    assert m.rank_weighted_divergence(["a","b"],["b","a"]) > 0
