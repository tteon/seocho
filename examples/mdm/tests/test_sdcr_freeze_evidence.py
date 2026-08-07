from __future__ import annotations
import importlib.util,sys
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"35_sdcr_freeze_evidence.py";S=importlib.util.spec_from_file_location("freeze",P);assert S and S.loader
m=importlib.util.module_from_spec(S);sys.modules[S.name]=m;S.loader.exec_module(m)

def test_value_nodes_rank_before_generic_nodes():
    value={"id":"b","labels":["Metric"],"props":{"name":"Revenue","value":"$1"}};generic={"id":"a","labels":["Entity"],"props":{"name":"A"}}
    assert sorted([generic,value],key=m.rank_node)[0] is value

def test_clean_drops_embedding_properties():
    assert m.clean({"name":"a","embedding_vector":[1,2]})=={"name":"a"}

def test_finance_abbreviation_expands_for_query_ranking():
    revenue={"id":"r","labels":["Revenue"],"props":{"name":"RevenueFromProducts_FY2023","value":"$2"}}
    eps={"id":"e","labels":["EPS"],"props":{"name":"EPS_FY2023","value":"$1"}}
    assert sorted([eps,revenue],key=lambda row:m.rank_node(row,"product rev 2023"))[0] is revenue
