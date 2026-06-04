#!/usr/bin/env python3
"""$0 measurement gates for the context-graph improvement cycle (BC3 decision).

The cycle's cheap leading indicators — NO LLM, pure structural Cypher over the
built graph. These gate most rounds for free; the expensive LLM judge runs only
every 2 rounds (per the agreed threshold). Grounded in the measured failure
modes (project_contextgraph_bc3_results): graph loses because extraction drops
stance edges / sent_date / decision-resolution structure, and floods MENTIONS.

Three families:
  - CQ coverage   : can the graph STRUCTURALLY answer each competency question
                    (= the eval slices)? fraction in [0,1].
  - SHACL-like    : conformance to required decision shapes (Cypher checks).
  - anti-pattern  : lints for the 5 LLM ontology anti-patterns
                    (reference_llm_ontology_antipatterns) — lower is better.

Run: python examples/contextgraph/cycle_metrics.py --db cgbc3minimaxm25 \
        --ws-prefix e1-bc3-full-decision-
"""
from __future__ import annotations
import argparse, os, sys, re
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ[k] = v

from seocho.store.graph import Neo4jGraphStore

_INFRA = {"Document", "DocumentVersion", "Chunk", "Section"}
_STANCE = ["SUPPORTS", "OPPOSES", "HAS_STANCE", "AGAINST", "FAVORS", "CONTRADICTS"]


def _q(gs, cy, w, db):
    try:
        return gs.query(cy, params={"w": w}, database=db)
    except Exception:
        return []


def cq_coverage(gs, w, db):
    """Competency questions = BC3 eval slices. Returns dict cq->bool (answerable)."""
    cqs = {}
    # CQ1 (E1_FACT who/when): a message with a non-empty sent_date property + a sender
    r = _q(gs, "MATCH (m:EmailMessage {_workspace_id:$w}) WHERE m.sent_date IS NOT NULL AND m.sent_date<>'' "
               "RETURN count(m) AS c", w, db)
    snt = _q(gs, "MATCH (:Person {_workspace_id:$w})-[:SENT]->(:EmailMessage {_workspace_id:$w}) RETURN count(*) AS c", w, db)
    cqs["CQ1_who_when"] = bool(r and r[0]["c"] > 0 and snt and snt[0]["c"] > 0)
    # CQ2 (E3_PROPOSALS who proposed what)
    r = _q(gs, "MATCH (:Person {_workspace_id:$w})-[:PROPOSES]->(:Proposal {_workspace_id:$w}) RETURN count(*) AS c", w, db)
    cqs["CQ2_proposals"] = bool(r and r[0]["c"] > 0)
    # CQ3 (E4_POSITIONS who for/against) — needs a stance edge
    sc = _q(gs, f"MATCH (a {{_workspace_id:$w}})-[r]->(b {{_workspace_id:$w}}) WHERE type(r) IN {_STANCE} RETURN count(r) AS c", w, db)
    cqs["CQ3_positions"] = bool(sc and sc[0]["c"] > 0)
    # CQ4 (E2_SUMMARY decisions/outcome) — a non-empty Decision linked to a Proposal
    r = _q(gs, "MATCH (d:Decision {_workspace_id:$w}) WHERE d.name IS NOT NULL AND d.name<>'' "
               "OPTIONAL MATCH (d)-[rel]->(p:Proposal {_workspace_id:$w}) "
               "RETURN count(DISTINCT d) AS nd, count(rel) AS nlink", w, db)
    cqs["CQ4_decisions"] = bool(r and r[0]["nd"] > 0 and r[0]["nlink"] > 0)
    return cqs


def shacl_conformance(gs, w, db):
    """SHACL-like decision shapes (Cypher). Returns dict shape->pass(bool)."""
    sh = {}
    msgs = _q(gs, "MATCH (m:EmailMessage {_workspace_id:$w}) RETURN count(m) AS n", w, db)
    nmsg = msgs[0]["n"] if msgs else 0
    dated = _q(gs, "MATCH (m:EmailMessage {_workspace_id:$w}) WHERE m.sent_date IS NOT NULL AND m.sent_date<>'' RETURN count(m) AS n", w, db)
    # SH1: every EmailMessage has a sent_date property
    sh["SH1_msg_sent_date"] = bool(nmsg > 0 and dated and dated[0]["n"] == nmsg)
    # SH2: >=1 PROPOSES
    r = _q(gs, "MATCH (:Person {_workspace_id:$w})-[:PROPOSES]->(:Proposal {_workspace_id:$w}) RETURN count(*) AS c", w, db)
    sh["SH2_has_proposes"] = bool(r and r[0]["c"] > 0)
    # SH3: >=1 stance edge
    sc = _q(gs, f"MATCH (a {{_workspace_id:$w}})-[r]->(b {{_workspace_id:$w}}) WHERE type(r) IN {_STANCE} RETURN count(r) AS c", w, db)
    sh["SH3_has_stance"] = bool(sc and sc[0]["c"] > 0)
    # SH4: well-formedness of any Decisions PRESENT (non-empty name + linked to a
    # Proposal). Domain-expert fix: do NOT force decision EXISTENCE (only ~4/40 BC3
    # threads actually resolve — forcing it manufactures decisions). So SH4 is
    # measured ONLY over workspaces that have ≥1 Decision (well-formedness), and
    # decision PRESENCE is reported separately (`has_decision`), never required.
    # This removes the vacuous-true pass without test-leakage.
    dec = _q(gs, "MATCH (d:Decision {_workspace_id:$w}) RETURN count(d) AS n", w, db)
    ndec = dec[0]["n"] if dec else 0
    good = _q(gs, "MATCH (d:Decision {_workspace_id:$w}) WHERE d.name IS NOT NULL AND d.name<>'' "
                  "AND (d)--(:Proposal {_workspace_id:$w}) RETURN count(DISTINCT d) AS n", w, db)
    sh["SH4_decision_wellformed"] = None if ndec == 0 else bool(good and good[0]["n"] == ndec)
    sh["_has_decision"] = ndec > 0  # reported as presence rate, NOT a conformance gate
    return sh


def antipattern_lints(gs, w, db):
    """5 anti-pattern lints (reference_llm_ontology_antipatterns). Lower=better."""
    labs = [r["l"] for r in _q(gs, "MATCH (n {_workspace_id:$w}) RETURN DISTINCT labels(n)[0] AS l", w, db) if r["l"]]
    ent_labs = [l for l in labs if l not in _INFRA]
    rels = [r["t"] for r in _q(gs, "MATCH (a {_workspace_id:$w})-[r]->(b {_workspace_id:$w}) RETURN DISTINCT type(r) AS t", w, db)]
    # AP5 fix (domain-expert): MENTIONS is mostly PROVENANCE plumbing (Chunk→entity),
    # not an LLM co-occurrence edge — so measure the generic-fallback fraction over
    # GENERATED edges only (exclude edges whose START node is an infra label).
    infra_list = list(_INFRA)
    gen = {r["t"]: r["c"] for r in _q(
        gs, f"MATCH (a {{_workspace_id:$w}})-[r]->(b {{_workspace_id:$w}}) "
            f"WHERE NOT labels(a)[0] IN {infra_list} RETURN type(r) AS t, count(*) AS c", w, db)}
    total_gen = sum(gen.values()) or 1
    generic = gen.get("MENTIONS", 0) + gen.get("RELATED_TO", 0)
    # property keys across entity nodes
    propkeys = set()
    for row in _q(gs, "MATCH (n {_workspace_id:$w}) WITH keys(n) AS ks UNWIND ks AS k RETURN DISTINCT k AS k", w, db):
        if row["k"] and not row["k"].startswith("_"):
            propkeys.add(row["k"])

    def _norm(s):  # for near-dup property detection
        return re.sub(r"[^a-z0-9]", "", s.lower())
    norm_groups = defaultdict(list)
    for k in propkeys:
        norm_groups[_norm(k)].append(k)
    dup_props = sum(len(v) - 1 for v in norm_groups.values() if len(v) > 1)
    # naming convention mix among labels+rels
    def _conv(s):
        if "_" in s: return "snake"
        if s[:1].isupper() and any(c.islower() for c in s): return "Pascal"
        if s.isupper(): return "UPPER"
        return "other"
    convs = {_conv(x) for x in (ent_labs + rels) if x}
    # concept-instance proxy: entity labels that look like instances (multi-word / digits)
    instanceish = [l for l in ent_labs if (" " in l or any(c.isdigit() for c in l))]
    # grounding lint (anti-fabrication, §20): fraction of decision-bearing nodes
    # (Proposal/Decision/Stance) carrying a source_quote/source_msg property.
    g = _q(gs, "MATCH (n {_workspace_id:$w}) WHERE labels(n)[0] IN ['Proposal','Decision','Stance'] "
               "RETURN count(n) AS n, "
               "sum(CASE WHEN n.source_quote IS NOT NULL AND n.source_quote<>'' "
               "OR n.source_msg IS NOT NULL THEN 1 ELSE 0 END) AS grounded", w, db)
    ng = g[0]["n"] if g else 0
    grounded_frac = (g[0]["grounded"] / ng) if (g and ng) else 0.0

    return {
        "AP1_entity_label_count": len(ent_labs),            # hierarchy explosion proxy
        "AP2_naming_conventions": len(convs),               # >1 = inconsistent taxonomy
        "AP3_dup_property_keys": dup_props,                 # property sprawl
        "AP4_instanceish_labels": len(instanceish),         # concept-instance confusion
        "AP5_generic_edge_fraction": round(generic / total_gen, 3),  # generated MENTIONS/RELATED_TO over GENERATED edges
        "grounded_fraction": round(grounded_frac, 3),       # anti-fabrication: source-quoted decision nodes
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="cgbc3minimaxm25")
    ap.add_argument("--ws-prefix", default="e1-bc3-full-decision-")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    gs = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    try:
        wss = [r["w"] for r in gs.query(
            "MATCH (n) WHERE n._workspace_id STARTS WITH $p RETURN DISTINCT n._workspace_id AS w ORDER BY w",
            params={"p": args.ws_prefix}, database=args.db)]
        if args.limit:
            wss = wss[: args.limit]
        cq_acc = defaultdict(int); ap_acc = defaultdict(float)
        sh_pass = defaultdict(int); sh_applic = defaultdict(int)  # conformance over APPLICABLE workspaces
        has_dec = 0
        cov_sum = 0.0
        for w in wss:
            cqs = cq_coverage(gs, w, args.db)
            shs = shacl_conformance(gs, w, args.db)
            aps = antipattern_lints(gs, w, args.db)
            cov_sum += sum(cqs.values()) / len(cqs)
            for k, v in cqs.items(): cq_acc[k] += int(v)
            has_dec += int(shs.pop("_has_decision", False))
            for k, v in shs.items():
                if v is None:  # shape not applicable to this workspace (e.g. no Decision)
                    continue
                sh_applic[k] += 1; sh_pass[k] += int(v)
            for k, v in aps.items(): ap_acc[k] += v
        n = len(wss)
        label = "round-0 baseline (corrected metric)"
        print(f"== cycle_metrics {label}: {n} workspaces ({args.ws_prefix}) ==\n")
        print(f"CQ COVERAGE (mean across workspaces): {cov_sum/n:.1%}")
        for k in sorted(cq_acc): print(f"  {k:<22} answerable in {cq_acc[k]}/{n} ({cq_acc[k]/n:.0%})")
        print("\nSHACL-LIKE CONFORMANCE (pass / applicable workspaces):")
        for k in sorted(sh_pass):
            ap_ = sh_applic[k]
            print(f"  {k:<24} {sh_pass[k]}/{ap_} ({(sh_pass[k]/ap_ if ap_ else 0):.0%})")
        print(f"  decision_presence_rate   {has_dec}/{n} ({has_dec/n:.0%})  [reported, NOT required]")
        print("\nANTI-PATTERN LINTS (mean per workspace; lower=better except grounded_fraction):")
        for k in sorted(ap_acc): print(f"  {k:<26} {ap_acc[k]/n:.3f}")
    finally:
        gs.close()


if __name__ == "__main__":
    main()
