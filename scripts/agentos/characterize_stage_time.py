"""Memory-plane stage-time characterization: the governance-tax analog of the serving
paper's prefill/decode split. Runs N medical questions through the governed arm and
reports mean wall-time per stage: resolve_schema / generate_llm / guardrail /
entity_resolve / execute_graph / synthesize_llm."""
import importlib.util, json, os, sys, statistics as st
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))
for alias, fn in [("mx","e2e_arm_organ_matrix"), ("med","e2e_arm_organ_medical"), ("mi","index_graphrag_medical")]:
    spec = importlib.util.spec_from_file_location(alias, os.path.join(_ROOT,"scripts","agentos",fn+".py"))
    globals()[alias] = importlib.util.module_from_spec(spec); spec.loader.exec_module(globals()[alias])
mx._load_mara()
from seocho import Seocho
onto = mi.medical_ontology()
client = Seocho.local(onto, llm="mara/gpt-oss-120b", graph="bolt://localhost:17687",
                      neo4j_user="neo4j", neo4j_password="h0gatepass",
                      api_key=os.environ.get("MARA_API_KEY"), workspace_id="med")
mx.wire_rcu(client, onto, "med")
qs = med._sample(4)[:12]          # 12 questions, mixed types
stages = {}
rows_out = []
for q in qs:
    try:
        client.ask(q["question"], engine="structured", database="medicallpg")
        sm = client.last_query_metadata["structured"].get("stage_ms", {})
        rows_out.append({"qid": q["id"], **sm})
        for k, v in sm.items(): stages.setdefault(k, []).append(v)
        print(f"  {q['id']}: " + " ".join(f"{k}={v:.0f}ms" for k, v in sm.items()), flush=True)
    except Exception as e:
        print(f"  {q['id']} ERR {str(e)[:80]}", flush=True)
total = sum(st.mean(v) for v in stages.values())
print(f"\n=== stage breakdown (N={len(rows_out)}, mean ms | share) ===", flush=True)
for k in ["resolve_schema","generate_llm","guardrail","entity_resolve","execute_graph","synthesize_llm"]:
    if k in stages:
        m = st.mean(stages[k])
        print(f"  {k:16s} {m:8.1f}ms  {100*m/total:5.1f}%", flush=True)
gov = sum(st.mean(stages[k]) for k in ("resolve_schema","guardrail","entity_resolve") if k in stages)
llm = sum(st.mean(stages[k]) for k in ("generate_llm","synthesize_llm") if k in stages)
print(f"\n  GOVERNANCE TAX = {gov:.1f}ms ({100*gov/total:.1f}%) vs LLM {llm:.1f}ms ({100*llm/total:.1f}%) "
      f"vs graph {st.mean(stages.get('execute_graph',[0])):.1f}ms", flush=True)
json.dump(rows_out, open(os.path.join(_ROOT,"outputs","agentos","stage_time_characterization.json"),"w"), indent=2)
print("=== wrote stage_time_characterization.json ===", flush=True)
