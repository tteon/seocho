"""Probe 1' — cross-tenant HOMONYM semantics (workspace organ as a MEANING boundary).

Reframed per hadry: not adversarial poisoning, but the realistic multi-tenant fact that
the SAME name denotes DIFFERENT things per department. "Atlas" is Engineering's
deployment pipeline (outage in March) and Sales' enterprise customer (renewed
contract). A Sales query about Atlas must be interpreted under Sales' meaning; without
the workspace organ the un-scoped read blends both referents and the answer attributes
Engineering's pipeline outage to the customer — cross-domain MISINTERPRETATION, no
attacker required.

Deterministic metric: cross-talk = the OTHER department's marker facts appearing in
this department's answer (eng markers: pipeline/outage/SRE-rollback; sales markers:
renewal/contract/CSM). Judged nothing; markers only.
"""
import importlib.util, json, os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))
spec = importlib.util.spec_from_file_location(
    "matrix", os.path.join(_ROOT, "scripts", "agentos", "e2e_arm_organ_matrix.py"))
mx = importlib.util.module_from_spec(spec); spec.loader.exec_module(mx)
spec2 = importlib.util.spec_from_file_location(
    "medidx", os.path.join(_ROOT, "scripts", "agentos", "index_graphrag_medical.py"))
mi = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(mi)
mx._load_mara()

from seocho import NodeDef, Ontology, P, RelDef, Seocho
from seocho.query.arm_config import ArmConfig

def dept_ontology():
    U = lambda: {"name": P(str, unique=True)}
    return Ontology("dept", package_id="dept", version="1.0.0",
        nodes={"Entity": NodeDef(description="A named thing a department talks about "
                                 "(system, customer, project).", properties=U(),
                                 cross_source_unique=True),
               "Event": NodeDef(description="Something that happened (outage, renewal, "
                                "escalation, rollback).",
                                properties={"name": P(str, unique=True), "detail": P(str)}),
               "Team": NodeDef(description="A team or role.", properties=U())},
        relationships={
            "INVOLVED_IN": RelDef(description="An entity was involved in an event.",
                                  source="Entity", target="Event"),
            "OWNED_BY": RelDef(description="An entity is owned/managed by a team.",
                               source="Entity", target="Team")})

ENG_DOCS = [
    "Atlas is our internal deployment pipeline. Atlas is owned by the SRE team. "
    "Atlas was involved in the March outage: a bad canary config caused a rollback. "
    "The SRE team completed the Atlas rollback on March 14.",
]
SALES_DOCS = [
    "Atlas is our enterprise customer account (Atlas Corp). Atlas is owned by the "
    "CSM team. Atlas was involved in the Q1 contract renewal: Atlas renewed their "
    "annual contract and requested the SSO feature.",
]
ENG_MARKERS = ["pipeline", "canary", "rollback", "sre", "outage"]
SALES_MARKERS = ["renewal", "contract", "csm", "sso", "customer account"]
QUESTIONS = [
    "What is Atlas and which team owns it?",
    "What events was Atlas involved in recently?",
    "Who should I contact about Atlas?",
]

def main():
    uri, pw, db = "bolt://localhost:17687", "h0gatepass", "deptlpg"
    onto = dept_ontology()
    for ws, docs in (("dept_eng", ENG_DOCS), ("dept_sales", SALES_DOCS)):
        c = Seocho.local(onto, llm="mara/gpt-oss-120b", graph=uri, neo4j_user="neo4j",
                         neo4j_password=pw, api_key=os.environ.get("MARA_API_KEY"),
                         workspace_id=ws)
        c._engine.graph_store.ensure_database(db)
        drv = c._engine.graph_store._driver
        with drv.session(database=db) as s:
            s.run("MATCH (n {_workspace_id:$w}) DETACH DELETE n", w=ws)
        for i, d in enumerate(docs):
            r = c.add(d, source_type=f"{ws}__{i}")
            md = r.metadata if hasattr(r, "metadata") else {}
            print(f"  [{ws}] doc{i} nodes={md.get('nodes_created')} rels={md.get('relationships_created')}", flush=True)

    # the SALES user asks; cross-talk = ENG markers in the sales answer
    client = Seocho.local(onto, llm="mara/gpt-oss-120b", graph=uri, neo4j_user="neo4j",
                          neo4j_password=pw, api_key=os.environ.get("MARA_API_KEY"),
                          workspace_id="dept_sales")
    mx.wire_rcu(client, onto, "dept_sales")
    results = []
    for arm in (ArmConfig.governed(), ArmConfig.governed().without("workspace")):
        client._engine._structured_arm = arm
        per_q = []
        for q in QUESTIONS:
            try:
                ans = str(client.ask(q, engine="structured", database=db))
                md = client.last_query_metadata
                xt = [m for m in ENG_MARKERS if m in ans.lower()]
                ok = [m for m in SALES_MARKERS if m in ans.lower()]
                per_q.append({"q": q, "cross_talk": xt, "own_meaning": ok,
                              "rows": md["result_count"],
                              "answer_source": md["answer_source"], "answer": ans[:220]})
            except Exception as e:
                per_q.append({"q": q, "error": f"{type(e).__name__}: {str(e)[:120]}"})
        ok_rows = [r for r in per_q if "error" not in r]
        xtalk = sum(1 for r in ok_rows if r["cross_talk"])
        results.append({"arm": arm.name, "cross_talk_answers": xtalk, "n": len(ok_rows),
                        "per_q": per_q})
        print(f"\n### {arm.name}: cross-talk {xtalk}/{len(ok_rows)}", flush=True)
        for r in ok_rows:
            tag = "!! XTALK" if r["cross_talk"] else "   ok"
            print(f"  [{tag}] {r['q'][:44]:44s} xt={r['cross_talk']} :: {r['answer'][:110]}", flush=True)

    out = os.path.join(_ROOT, "outputs", "agentos", "probe_tenant_semantics_results.json")
    json.dump({"questions": QUESTIONS, "eng_markers": ENG_MARKERS,
               "sales_markers": SALES_MARKERS, "arms": results}, open(out, "w"), indent=2)
    print(f"\n=== wrote {out} ===", flush=True)

if __name__ == "__main__":
    main()
