from __future__ import annotations
import importlib.util
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"24_finder_cross_view_candidates.py"
S=importlib.util.spec_from_file_location("crossview",P); assert S and S.loader
m=importlib.util.module_from_spec(S); S.loader.exec_module(m)

def test_infer_issuer_uses_last_non_excluded_uppercase_token():
    assert m.infer_issuer("ASC policy affects FDX risk")=="FDX"

def test_build_candidates_is_all_cross_category_pairs_and_output_blind():
    rows=[{"case_id":"a","category":"Risk","query":"Risk for ACME"},
          {"case_id":"b","category":"Legal","query":"Legal for ACME"},
          {"case_id":"c","category":"Legal","query":"Other legal ACME"}]
    gold={x:{"query":x,"expected_answer":x+" gold"} for x in "abc"}
    out=m.build_candidates(rows,gold)
    assert len(out)==2
    assert all(row["selection_uses_model_outputs"] is False for row in out)
    assert all(row["human_validation_status"]=="pending" for row in out)
    assert all("required_gold_slots" in row for row in out)
