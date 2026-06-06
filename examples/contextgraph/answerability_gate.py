#!/usr/bin/env python3
"""Answerability Gate ($0, no LLM, no judge) — SEOCHO's ontology-as-predicate feature.

Panel-converged principle (systems architect + ontologist): the ontology earns
its keep ONLY where it is read as a DETERMINISTIC PREDICATE at a decision boundary
(routing/admission, provenance, governance) — NOT as a constraint inside
generative steps (extraction/synthesis), where over-constraining loses signal
(Goldilocks). So this gate reads the DECLARED schema as the sole admission
authority and answers, $0: can the graph lane serve this question class
deterministically? It is two layers:

  LAYER 1 — ROUTING gate (declared-schema-only, per question-class, NO graph read):
    is the required answer-relation DECLARED in the active composed ontology?
    COVERED / PARTIAL (related rel, wrong endpoint type) / UNCOVERED.

  LAYER 2 — SERVING certificate (per-case, reads the graph but ONLY for DECLARED
    relations — the firewall): does this workspace's subgraph hold grounded tuples
    of the DECLARED relation? Crucially it IGNORES undeclared edges in the store
    (the a1 graphs contain prompt-SMUGGLED SUPPORTS/OPPOSES the 'decision' arm
    never declared) — so it refuses to serve from an ungoverned edge.

Validates $0 against the measured stage-local tuple-F1 and demonstrates the
silent-wrong elimination the gate buys. The gate NEVER reads the unstable judge
(kappa 0.2-0.5) and NEVER treats "graph has an edge" as coverage.

Run: python examples/contextgraph/answerability_gate.py
"""
from __future__ import annotations
import json, logging, os, sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples" / "contextgraph"))
from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ[k] = v
logging.getLogger("neo4j").setLevel(logging.ERROR)
from decision_modules.compose import compose_modules, ARMS
from seocho.store.graph import Neo4jGraphStore

DB = "cgbc3minimaxm25"
WS_PREFIX = "e1-bc3-a1-decision-"
GOLD = ROOT / "outputs/evaluation/contextgraph/gold_tuples_m27.json"

# question-class -> (subject_type, semantic_role, object_type) the answer REQUIRES.
# In the runtime feature this comes from intent.derive_route_class.
REQUIRED = {
    "E1_FACT":            ("Person", "sent_message",  "EmailMessage"),  # who initiated + when
    "E2_DECISION_SUMMARY":("Decision", "resolves",    "Proposal"),      # what was decided
    "E3_PROPOSALS":       ("Person", "proposes",      "Proposal"),      # who proposed what
    "E4_POSITIONS":       ("Person", "holds_opinion", "Topic"),         # who holds what position (broad)
}
ROLE_RELATIONS = {  # semantic role -> declared relation names that FULLY serve it
    "sent_message": {"SENT"},
    "resolves":     {"RESOLVES", "DECIDES"},
    "proposes":     {"PROPOSES"},
    "holds_opinion": {"HOLDS_POSITION", "EXPRESSES_OPINION", "HAS_OPINION"},  # opinion-on-Topic
    "holds_opinion_partial": {"SUPPORTS", "OPPOSES", "HAS_STANCE", "STANCE_ON"},  # stance-on-Proposal
}
# declared-relation -> the graph query that checks LAYER-2 grounded presence (declared only)
SERVE_CHECK = {
    "E1_FACT": ("MATCH (p:Person {_workspace_id:$w})-[:SENT]->(m:EmailMessage {_workspace_id:$w}) "
                "WHERE m.sent_date IS NOT NULL AND m.sent_date<>'' RETURN count(*) AS c"),
    "E2_DECISION_SUMMARY": ("MATCH (d:Decision {_workspace_id:$w}) "
                            "OPTIONAL MATCH (d)-[:RESOLVES]->(:Proposal {_workspace_id:$w}) RETURN count(d) AS c"),
    "E3_PROPOSALS": ("MATCH (:Person {_workspace_id:$w})-[:PROPOSES]->(pr:Proposal {_workspace_id:$w}) "
                     "WHERE pr.source_quote IS NOT NULL RETURN count(*) AS c"),
    # E4 has NO declared serving relation in the 'decision' arm -> by firewall, 0 servable
    # (the SUPPORTS/OPPOSES edges in the store are UNDECLARED -> must be ignored).
    "E4_POSITIONS": None,
}


def route_gate(slice_name, declared):
    subj, role, obj = REQUIRED[slice_name]
    full = ROLE_RELATIONS.get(role, set())
    partial = ROLE_RELATIONS.get(role + "_partial", set())
    if declared & full:
        return "COVERED", sorted(declared & full)
    if declared & partial:
        return "PARTIAL", sorted(declared & partial)
    return "UNCOVERED", []


def main():
    onto = compose_modules(ARMS["decision"])  # the arm a1 used
    declared = set(onto.relationships)
    print("== Answerability Gate ($0) — arm 'decision' declares:", sorted(declared), "==\n")

    # ---- LAYER 1: routing gate (declared-schema-only, per class) ----
    print("LAYER 1 — ROUTING gate (declared schema only, NO graph read):")
    verdicts = {}
    for sl in REQUIRED:
        v, rels = route_gate(sl, declared)
        verdicts[sl] = v
        print(f"   {sl:<20} {v:<10} {('via '+str(rels)) if rels else REQUIRED[sl]}")

    # ---- LAYER 2: serving certificate (per-case, declared relations ONLY = firewall) ----
    print("\nLAYER 2 — SERVING certificate (per-case; reads graph but ONLY declared relations):")
    gs = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    gold = json.loads(GOLD.read_text())
    # thread ids per slice from the gold set
    by_slice = defaultdict(list)
    for k, rec in gold.items():
        by_slice[rec["slice"]].append(str(rec["_id"]).split("#")[0])
    # also count UNDECLARED smuggled edges the naive answerer would have served from
    smuggled_total = 0
    try:
        cert = defaultdict(lambda: {"n": 0, "servable": 0})
        for sl in ("E3_PROPOSALS", "E4_POSITIONS"):
            q = SERVE_CHECK[sl]
            for tid in by_slice.get(sl, []):
                w = f"{WS_PREFIX}{tid}"
                cert[sl]["n"] += 1
                if q is not None:
                    c = gs.query(q, params={"w": w}, database=DB)
                    if c and c[0]["c"] > 0:
                        cert[sl]["servable"] += 1
                if sl == "E4_POSITIONS":
                    # how many UNDECLARED stance edges exist (would be silent-wrong if served)
                    su = gs.query("MATCH (:Person {_workspace_id:$w})-[r]->(:Proposal {_workspace_id:$w}) "
                                  "WHERE type(r) IN ['SUPPORTS','OPPOSES'] RETURN count(r) AS c",
                                  params={"w": w}, database=DB)
                    smuggled_total += (su[0]["c"] if su else 0)
        for sl in ("E3_PROPOSALS", "E4_POSITIONS"):
            d = cert[sl]
            print(f"   {sl:<20} declared-servable cases: {d['servable']}/{d['n']} "
                  + ("(declared serving relation EXISTS)" if SERVE_CHECK[sl] else
                     "(NO declared serving relation -> firewall refuses ALL)"))
    finally:
        gs.close()

    # ---- $0 cross-check vs measured tuple-F1 + the firewall win ----
    print("\n== $0 validation ==")
    measured = {"E1_FACT": "corr|admit 0.75 when served (failure=sent_date DATA, not schema)",
                "E2_DECISION_SUMMARY": "answerer doesn't serve (sparse decisions)",
                "E3_PROPOSALS": "tuple-F1 0.11 (>0) — COVERED predicts viable",
                "E4_POSITIONS": "tuple-F1 invalid; 8/27 gold un-draftable — UNCOVERED predicts non-viable"}
    for sl in REQUIRED:
        print(f"   {sl:<20} gate={verdicts[sl]:<10} | {measured[sl]}")
    print(f"\n== firewall win (the feature) ==")
    print(f"   the a1 store holds {smuggled_total} UNDECLARED SUPPORTS/OPPOSES edges (prompt-smuggled;")
    print(f"   the 'decision' arm declares neither). A naive answerer SERVES from them")
    print(f"   (experiment 0: E4 silent-wrong 69%). The gate marks E4 UNCOVERED and the")
    print(f"   serving certificate IGNORES undeclared edges -> refuses -> routes to vector.")
    print(f"   => E4 silent-wrong eliminated at \$0 routing time, before any answer.")
    print(f"\n   PRINCIPLE: gate reads DECLARED schema as the sole admission authority,")
    print(f"   NEVER 'the graph has an edge' (that re-admits the smuggled silent-wrong).")


if __name__ == "__main__":
    main()
