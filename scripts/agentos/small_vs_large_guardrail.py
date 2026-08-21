"""Small experiment: does the ontology guardrail let a SMALL model do text2cypher?

Hybrid thesis (API + on-prem): the governance/admission plane is deterministic (0 LLM),
and grounded text2cypher is a CONSTRAINED generation (declared ids, $params, LIMIT) that
the guardrail+repair loop backstops. So a small on-prem model should suffice for the
generator, keeping sensitive graph access local; a large API model is only needed for
final answer synthesis. We test the load-bearing claim: with the guardrail on, a small
generator's answered-rate approaches a large generator's.

Size proxy via MARA (no local vLLM setup needed for this small run):
  LARGE = gpt-oss-120b   SMALL = gemma-4-31B

Three conditions on the medical graph (medicallpg), governed arm unless noted:
  A LARGE + guardrail        (reference ceiling)
  B SMALL + guardrail        (the hybrid proposal)
  C SMALL - guardrail        (small alone — how much the guardrail closes the gap)

Metrics (per condition): answered-rate, mean repair attempts, gold_hit, guardrail
rejects. If B approaches A while C lags, the ontology guardrail is what makes the
small model practical — the hybrid claim.
"""

import importlib.util
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))

for alias, fn in [("mx", "e2e_arm_organ_matrix"), ("med", "e2e_arm_organ_medical"),
                  ("mi", "index_graphrag_medical")]:
    spec = importlib.util.spec_from_file_location(alias, os.path.join(_ROOT, "scripts", "agentos", fn + ".py"))
    globals()[alias] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(globals()[alias])
mx._load_mara()

from seocho import Seocho
from seocho.query.arm_config import ArmConfig


def run_condition(name, model, guardrail, questions, judge_llm, db="medicallpg"):
    onto = mi.medical_ontology()
    client = Seocho.local(onto, llm=model, graph="bolt://localhost:17687", neo4j_user="neo4j",
                          neo4j_password="h0gatepass", api_key=os.environ.get("MARA_API_KEY"),
                          workspace_id="med")
    mx.wire_rcu(client, onto, "med")
    arm = ArmConfig.governed() if guardrail else ArmConfig.governed().without("guardrail")
    client._engine._structured_arm = arm
    rows = []
    for q in questions:
        try:
            ans = client.ask(q["question"], engine="structured", database=db)
            m = client.last_query_metadata["structured"]
            v = mx.judge(judge_llm, q["question"], ans, [q["answer"]])
            rows.append({"answered": client.last_query_metadata["answer_source"] == "structured",
                         "repairs": m.get("repair_attempts", 0),
                         "rejected": m.get("guardrail_rejected", False),
                         "coverage": v["coverage"],
                         "gold_hit": med._gold_hit(str(ans), q["answer"], q["question"])})
        except Exception as e:
            rows.append({"error": str(e)[:80]})
    ok = [r for r in rows if "error" not in r]
    n = len(ok) or 1
    s = {"condition": name, "model": model, "guardrail": guardrail, "n": len(ok),
         "answered": sum(1 for r in ok if r["answered"]),
         "gold_hits": sum(1 for r in ok if r["gold_hit"]),
         "mean_coverage": round(sum(r["coverage"] for r in ok) / n, 3),
         "mean_repairs": round(sum(r["repairs"] for r in ok) / n, 2),
         "rejects": sum(1 for r in ok if r["rejected"]),
         "errors": len(rows) - len(ok)}
    print(f"### {name}: model={model} guardrail={guardrail}", flush=True)
    print(f"    answered={s['answered']}/{s['n']} gold_hits={s['gold_hits']} cov={s['mean_coverage']} "
          f"repairs={s['mean_repairs']} rejects={s['rejects']} err={s['errors']}", flush=True)
    return s


def main():
    from seocho.store.llm import create_llm_backend
    judge_llm = create_llm_backend(provider="mara", model="DeepSeek-V3.1",
                                   api_key=os.environ.get("MARA_API_KEY"))
    questions = med._sample(5)          # 15 questions, mixed types
    print(f"questions={len(questions)}", flush=True)

    LARGE, SMALL = "mara/gpt-oss-120b", "mara/gemma-4-31B-it"
    results = [
        run_condition("A_large_guardrail", LARGE, True, questions, judge_llm),
        run_condition("B_small_guardrail", SMALL, True, questions, judge_llm),
        run_condition("C_small_noguardrail", SMALL, False, questions, judge_llm),
    ]
    out = os.path.join(_ROOT, "outputs", "agentos", "small_vs_large_guardrail.json")
    json.dump(results, open(out, "w"), indent=2)
    A, B, C = results
    print("\n=== hybrid claim check ===", flush=True)
    print(f"  A large+guardrail : answered {A['answered']}/{A['n']}", flush=True)
    print(f"  B small+guardrail : answered {B['answered']}/{B['n']}  "
          f"(gap to large: {A['answered']-B['answered']})", flush=True)
    print(f"  C small alone     : answered {C['answered']}/{C['n']}  "
          f"(guardrail closes: {B['answered']-C['answered']})", flush=True)
    print(f"=== wrote {out} ===", flush=True)


if __name__ == "__main__":
    main()
