from __future__ import annotations
import importlib.util,sys
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"39_cross_view_evidence_audit.py";S=importlib.util.spec_from_file_location("audit39",P);assert S and S.loader
m=importlib.util.module_from_spec(S);sys.modules[S.name]=m;S.loader.exec_module(m)

def test_slim_retains_provenance_but_drops_embeddings():
    result=m.slim({"name":"Revenue","value":"$1","provider_id":"p","embedding_vector":[1]})
    assert result=={"name":"Revenue","value":"$1","provider_id":"p"}
