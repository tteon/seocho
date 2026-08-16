"""text2cypher grounding via shared intern table + competency questions (seocho-ia4)."""
from __future__ import annotations
from seocho.ontology.core import NodeDef, Ontology, P
from seocho.index.identity import compute_node_identity
from seocho.index.shared_intern import SharedInternTable
from seocho.query.intern_grounding import (
    extract_mentions, resolve_mentions, rank_competency_questions, ground_request,
)


def _onto():
    return Ontology(name="fin", nodes={
        "Company": NodeDef(properties={"name": P(str)}, identity_keys=["name"]),
    }, relationships={})


def _table(onto, names, ws="w"):
    t = SharedInternTable()
    for nm in names:
        ident = compute_node_identity("Company", {"name": nm}, ["name"])
        t.intern(ws, ident, ident)
    return t


def test_extract_mentions():
    ms = extract_mentions("What revenue did Apple Inc. and Bank of America report?")
    assert any("Apple" in m for m in ms)
    assert any("Bank of America" in m for m in ms)


def test_resolve_hits_and_misses():
    onto = _onto()
    t = _table(onto, ["Apple", "Microsoft"])
    resolved, unresolved = resolve_mentions(
        "Compare Apple and Tesla revenue", intern_table=t, ontology=onto, workspace_id="w")
    assert any(m == "Apple" for m, _ in resolved)         # exact -> resolved to canonical
    assert "Tesla" in unresolved                          # not in namespace -> can't-find signal


def test_competency_question_intent_ranking():
    cqs = ["What is the total revenue of a company?",
           "Which regulator enforces a regulation?",
           "Who are the board members of a company?"]
    ranked = rank_competency_questions("What revenue did the company report?", cqs, top_k=2)
    assert ranked and "revenue" in ranked[0][0].lower()   # revenue CQ ranks top


def test_ground_request_combines_all():
    onto = _onto()
    t = _table(onto, ["Apple"])
    g = ground_request("What revenue did Apple and Globex report?",
                       intern_table=t, ontology=onto, workspace_id="w",
                       competency_questions=["What is the total revenue of a company?"])
    d = g.to_dict()
    assert d["resolved"] and d["resolved"][0]["mention"] == "Apple"
    assert "Globex" in d["unresolved"]
    assert d["intents"] and d["resolution_rate"] == 0.5
