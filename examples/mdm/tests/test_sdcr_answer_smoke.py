from __future__ import annotations
import importlib.util,sys
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"34_sdcr_answer_smoke.py";S=importlib.util.spec_from_file_location("smoke",P);assert S and S.loader
m=importlib.util.module_from_spec(S);sys.modules[S.name]=m;S.loader.exec_module(m)

def test_relevant_fact_filter_requires_period_and_metric():
    record={"survivorship":{"golden":[{"metric":"RevenueFromProducts_FY2022","period":"fy2022"},{"metric":"Other","period":"fy2022"}]}}
    assert len(m.relevant_xyl_facts(record))==1
