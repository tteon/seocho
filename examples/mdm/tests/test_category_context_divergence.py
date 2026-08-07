from __future__ import annotations
import importlib.util
from collections import Counter
from pathlib import Path
import pytest

P=Path(__file__).resolve().parents[1]/"23_category_context_divergence.py"
S=importlib.util.spec_from_file_location("ctxdiv",P); assert S and S.loader
m=importlib.util.module_from_spec(S); S.loader.exec_module(m)

def test_normalize_and_context_key():
    assert m.normalize_name("ACME, Inc.")=="acme inc"
    assert m.context_key({"name":"ACME, Inc.","labels":["Issuer"]})=="acme inc|Issuer"
    assert m.is_identity_candidate("acme inc|Issuer") is True
    assert m.is_identity_candidate("the company|LegalEntity") is False
    assert m.is_identity_candidate("reporting company|LegalEntity") is False

def test_jensen_shannon_bounds_and_identity():
    assert m.jensen_shannon(Counter({"a":2}),Counter({"a":3}))==0
    assert m.jensen_shannon(Counter({"a":1}),Counter({"b":1}))==pytest.approx(1)

def test_structural_divergence_detects_changed_context():
    nodes=[
      {"id":"a1","name":"ACME","labels":["Issuer"],"category":"Risk"},
      {"id":"a2","name":"Risk X","labels":["Risk"],"category":"Risk"},
      {"id":"b1","name":"ACME","labels":["Issuer"],"category":"Legal"},
      {"id":"b2","name":"Case Y","labels":["Litigation"],"category":"Legal"},
    ]
    edges=[{"source":"a1","target":"a2","type":"EXPOSED_TO"},
           {"source":"b1","target":"b2","type":"PARTY_TO"}]
    rows=m.structural_divergence(nodes,edges)
    assert len(rows)==1
    assert rows[0]["hop_js_divergence"]["1"]==pytest.approx(1)

def test_provenance_profiles_keep_model_prompt_and_ontology_explicit():
    profiles=m.provenance_profiles([{"category":"Risk","provider_id":"p1","model":"m1",
      "prompt_id":"prompt-a","ontology_hash":"hash-a","ontology_modules":["be","fbc"]}])
    assert profiles["Risk"]["models"]==["m1"]
    assert profiles["Risk"]["prompt_ids"]==["prompt-a"]
    assert profiles["Risk"]["ontology_modules"]==["be","fbc"]

def test_percentiles_are_size_normalized():
    out=m._percentiles({"low":1.0,"mid":2.0,"high":3.0})
    assert out=={"low":0.0,"mid":0.5,"high":1.0}

def test_normalize_modules_handles_database_string_storage():
    assert m.normalize_modules("be,fbc,ind,acc") == ["acc","be","fbc","ind"]

def test_ppr_retrieval_finds_disjoint_category_contexts():
    nodes=[
      {"id":"a1","name":"ACME","labels":["Issuer"],"category":"Risk"},
      {"id":"a2","name":"Operational Risk","labels":["Risk"],"category":"Risk"},
      {"id":"b1","name":"ACME","labels":["Issuer"],"category":"Legal"},
      {"id":"b2","name":"Antitrust Case","labels":["Lawsuit"],"category":"Legal"},
    ]
    edges=[{"source":"a1","target":"a2","type":"EXPOSED_TO","category":"Risk"},
           {"source":"b1","target":"b2","type":"DEFENDANT_IN","category":"Legal"}]
    rows=m.personalized_pagerank_retrieval(nodes,edges,top_k=2)
    assert len(rows)==1
    assert rows[0]["top_k_jaccard"]==0
    assert rows[0]["top_k_divergence"]==1
