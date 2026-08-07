from __future__ import annotations
import importlib.util,sys
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"41_sdcr_development_retrieval.py";S=importlib.util.spec_from_file_location("retrieval41",P);assert S and S.loader
m=importlib.util.module_from_spec(S);sys.modules[S.name]=m;S.loader.exec_module(m)

def test_lexical_fallback_is_deterministic_without_edges():
    view={"nodes":[{"id":"a","labels":["EPS"],"props":{"name":"EPS"}},{"id":"b","labels":["Revenue"],"props":{"name":"Revenue"}}],"triples":[]}
    ranked,method=m.personalized_rank(view,"revenue")
    assert method=="lexical_fallback" and ranked[0]["id"]=="b"

def test_coverage_distinguishes_slots():
    nodes=[{"id":"a","labels":["Revenue"],"props":{"value":"$100"}}]
    assert m.coverage(nodes,"Revenue was $100")["number_recall"]==1
