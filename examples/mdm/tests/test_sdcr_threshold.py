from __future__ import annotations
import importlib.util, sys
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"28_sdcr_threshold.py"
S=importlib.util.spec_from_file_location("sdcr",P); assert S and S.loader
m=importlib.util.module_from_spec(S);sys.modules[S.name]=m;S.loader.exec_module(m)

def test_finite_sample_null_tail_has_plus_one_correction():
    assert m.null_tail_pvalue(.9,[.1,.2,.3]) == .25
    assert m.null_tail_pvalue(.2,[.1,.2,.3]) == .75

def test_slot_gap_triggers_multi_without_divergence():
    result=m.trigger(best_single_slot_coverage=.5,verify_required=False,divergence_pvalues=[])
    assert result["multi_agent"] and result["slot_trigger"]

def test_divergence_requires_material_verification_slot():
    assert not m.trigger(best_single_slot_coverage=1,verify_required=False,divergence_pvalues=[.001])["multi_agent"]
    assert m.trigger(best_single_slot_coverage=1,verify_required=True,divergence_pvalues=[.001])["multi_agent"]

def test_holm_stops_after_first_non_rejection():
    assert m.holm_rejections([.01,.04,.20],alpha=.05)==[True,False,False]
