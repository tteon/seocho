"""Held-out validation of Finding 1 (intern read-side). The resolve fix was designed
AFTER seeing run-1's failures on the original 21 questions — a same-data iteration
risk. This reruns ONLY governed vs governed-no-intern on a FRESH, non-overlapping
21-question sample (next 7 per type after the original 7), so the claim survives a
held-out test rather than being tuned to its own test set."""
import importlib.util, json, os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))
for name in ["matrix:e2e_arm_organ_matrix", "med:e2e_arm_organ_medical", "mi:index_graphrag_medical"]:
    alias, fn = name.split(":")
    spec = importlib.util.spec_from_file_location(alias, os.path.join(_ROOT, "scripts", "agentos", fn + ".py"))
    globals()[alias] = importlib.util.module_from_spec(spec); spec.loader.exec_module(globals()[alias])
matrix._load_mara()
from seocho import Seocho
from seocho.query.arm_config import ArmConfig
from seocho.store.llm import create_llm_backend

# held-out sample: per type, questions ranked 7..13 (original used 0..6)
qs_all = json.load(open("/home/hadry/openup/_graphrag_benchmark/Datasets/Questions/medical_questions.json"))
by = {t: [] for t in med._TYPES}
for q in sorted(qs_all, key=lambda x: x["id"]):
    t = q.get("question_type")
    if t in by and len(by[t]) < 14:
        by[t].append(q)
questions = [q for t in med._TYPES for q in by[t][7:14]]
print(f"held-out questions: {len(questions)} (ids disjoint from run set)", flush=True)

onto = mi.medical_ontology()
client = Seocho.local(onto, llm="mara/gpt-oss-120b", graph="bolt://localhost:17687",
                      neo4j_user="neo4j", neo4j_password="h0gatepass",
                      api_key=os.environ.get("MARA_API_KEY"), workspace_id="med")
matrix.wire_rcu(client, onto, "med")
judge_llm = create_llm_backend(provider="mara", model="DeepSeek-V3.1",
                               api_key=os.environ.get("MARA_API_KEY"))
out = {}
for arm in (ArmConfig.governed(), ArmConfig.governed().without("intern")):
    client._engine._structured_arm = arm
    rows = []
    for q in questions:
        try:
            ans = client.ask(q["question"], engine="structured", database="medicallpg")
            md = client.last_query_metadata
            v = matrix.judge(judge_llm, q["question"], ans, [q["answer"]])
            rows.append({"qid": q["id"], "answered": md["answer_source"] == "structured",
                         "coverage": v["coverage"]})
        except Exception as e:
            rows.append({"qid": q["id"], "error": str(e)[:100]})
    ok = [r for r in rows if "error" not in r]
    ans_n = sum(1 for r in ok if r["answered"])
    cov = sum(r["coverage"] for r in ok) / (len(ok) or 1)
    out[arm.name] = {"answered": ans_n, "n": len(ok), "coverage": round(cov, 3), "rows": rows}
    print(f"### {arm.name}: answered={ans_n}/{len(ok)} coverage={cov:.3f}", flush=True)
json.dump(out, open(os.path.join(_ROOT, "outputs", "agentos", "heldout_intern_check.json"), "w"), indent=2)
print("=== wrote heldout_intern_check.json ===", flush=True)
