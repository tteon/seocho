from __future__ import annotations
import importlib.util, sys
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"27_full_finder_multiagent_network.py"
S=importlib.util.spec_from_file_location("fullnet",P); assert S and S.loader
m=importlib.util.module_from_spec(S);sys.modules[S.name]=m;S.loader.exec_module(m)

def test_repeated_entities_are_output_blind_and_deterministic():
    nodes=[{"name":"acme","category":"Risk","observations":5},{"name":"acme","category":"Legal","observations":4},{"name":"beta","category":"Risk","observations":8},{"name":"beta","category":"Legal","observations":8}]
    assert m.repeated_entity_pairs(nodes,limit=2)==["beta","acme"]

def test_identity_filter_removes_generic_names():
    assert m.valid_identity("acme corporation")
    assert not m.valid_identity("the company")
