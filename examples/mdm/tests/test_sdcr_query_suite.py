from __future__ import annotations
import importlib.util,sys
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"30_sdcr_query_suite.py";S=importlib.util.spec_from_file_location("suite",P);assert S and S.loader
m=importlib.util.module_from_spec(S);sys.modules[S.name]=m;S.loader.exec_module(m)

def test_pending_candidates_are_not_accepted():
    data={"annotations":[{"candidate_id":"a","adjudicated":None},{"candidate_id":"b","adjudicated":{"decision":"accept"}}]}
    assert m.accepted_ids(data)=={"b"}
