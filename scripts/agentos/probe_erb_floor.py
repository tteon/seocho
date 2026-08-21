"""ERB contamination floor: can the generator answer Redwood's internal enterprise
questions CLOSED-BOOK (no graph)? ERB is synthetic company-internal decision data, so
the floor should be LOW — which would make judge-coverage a VALID organ metric on erb
(unlike the memorized public medical corpus, floor 0.90)."""
import importlib.util, json, os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))
spec = importlib.util.spec_from_file_location(
    "matrix", os.path.join(_ROOT, "scripts", "agentos", "e2e_arm_organ_matrix.py"))
mx = importlib.util.module_from_spec(spec); spec.loader.exec_module(mx)
mx._load_mara()
from seocho.store.llm import create_llm_backend, complete_with_task_hints
gen = create_llm_backend(provider="mara", model="gpt-oss-120b", api_key=os.environ.get("MARA_API_KEY"))
jud = create_llm_backend(provider="mara", model="DeepSeek-V3.1", api_key=os.environ.get("MARA_API_KEY"))
qs = [json.loads(l) for l in open(os.path.join(_ROOT, "outputs/agentos/erb_xsource_workingset/questions.jsonl")) if l.strip()]
covs = []
for q in qs:
    r = complete_with_task_hints(gen, system="Answer the question concisely in 1-3 sentences.",
                                 user=q["question"], temperature=0.0, reasoning_mode=False,
                                 task_hint="answer_synthesis")
    ans = getattr(r, "text", None) or str(r)
    v = mx.judge(jud, q["question"], ans, q.get("answer_facts", []))
    covs.append(v["coverage"])
    print(f"  {q['question_id']} cov={v['coverage']:.2f} :: {ans[:90]}", flush=True)
print(f"\nERB closed-book FLOOR: mean coverage = {sum(covs)/len(covs):.3f} over {len(covs)} questions", flush=True)
