from __future__ import annotations
import importlib.util,sys
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"37_full_finder_cross_view_pool.py";S=importlib.util.spec_from_file_location("pool",P);assert S and S.loader
m=importlib.util.module_from_spec(S);sys.modules[S.name]=m;S.loader.exec_module(m)

def case(cid,category,query,answer="gold"):
    return {"case_id":cid,"category":category,"query":query,"expected_answer":answer}

def test_axes_capture_disclosure_motivated_decisions():
    assert "liquidity_capital_allocation" in m.decision_axes("FDX liquidity and share repurchase")
    assert "enterprise_risk" in m.decision_axes("FDX cybersecurity risk")

def test_pool_is_cross_category_output_blind_and_issuer_disjoint():
    rows=[case("1","Legal","Legal settlement affects cash for ACME"),case("2","Shareholder return","Share repurchase affects cash for ACME"),case("3","Risk","Cyber risk for BETA"),case("4","Legal","Legal risk for BETA")]
    result=m.build_pool(rows,10)
    assert result["candidate_count"]==2
    assert all(row["required_categories"][0]!=row["required_categories"][1] for row in result["candidates"])
    assert all(row["selection_uses_model_outputs"] is False for row in result["candidates"])
    assert len({row["split"] for row in result["candidates"] if row["issuer"]=="ACME"})==1
