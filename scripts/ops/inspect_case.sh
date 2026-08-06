#!/usr/bin/env bash
# 케이스 하나의 전 여정을 사람이 검수할 수 있게 출력한다.
#   scripts/ops/inspect_case.sh 032ab4b7
set -euo pipefail
cd "$(dirname "$0")/../.."
CASE="${1:?usage: inspect_case.sh <case_id>}"
exec .venv/bin/python - "$CASE" <<'PY'
import json, sys, glob
cid = sys.argv[1]
import importlib.util
spec = importlib.util.spec_from_file_location("fi", "examples/mdm/11_index_providers.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
case = next(c for c in m.load_cases_full(seed=42) if c["case_id"] == cid)

W = 100
def head(t): print("\n" + "="*W + f"\n{t}\n" + "="*W)

head(f"① 데이터 입력 — FinDER 케이스 {cid} ({case['category']})")
print("질문:", case["query"])
print("정답:", case["expected_answer"][:400])
print(f"참조 지문 {len(case['references'])}개, 첫 지문 앞부분:")
print("  " + str(case["references"][0])[:300].replace("\n", "\n  "))

head("② 모델이 실제로 받은 추출 프롬프트 (트레이스 기록에서)")
shown = False
for d in sorted(glob.glob("outputs/minimal/*-reextract"), reverse=True):
    try:
        for line in open(d + "/trace.jsonl"):
            r = json.loads(line)
            if r.get("stage") == "llm.extract.request" and r.get("case") == cid:
                print(f"실행: {d.split('/')[-1]}  조건 {r['arm']} × {r['model_key']}")
                print(f"시스템 {r['system_chars']}자 (head):")
                print("  " + r["system_head"][:600].replace("\n", "\n  "))
                shown = True; break
    except FileNotFoundError:
        continue
    if shown: break
if not shown: print("  (이 케이스의 추출 트레이스 없음)")

head("③ 추출된 그래프 (스냅샷 — Neo4j 브라우저로도 확인 가능)")
for path in sorted(glob.glob(f"snapshots/*/*_{cid}.jsonl"))[:3]:
    tag_cond_model = path.split("/")[-2] + " " + path.split("/")[-1].rsplit("_", 1)[0]
    nodes = [json.loads(l) for l in open(path) if '"kind": "node"' in l]
    real = [n for n in nodes if not set(n["labels"]) & {"Chunk","Document","DocumentVersion","Section"}]
    print(f"\n[{tag_cond_model}] 노드 {len(real)}개 중 5개:")
    for n in real[:5]:
        p = n["props"]
        print(f"  ({n['labels'][0]}) {p.get('name','')!r}  value={p.get('value')!r}  period={p.get('period')!r}")

head("④ 답변 실험 — 조건별 실제 답과 채점")
for path in sorted(glob.glob(f"outputs/evaluation/answering/an*/*/{'*'}/{cid}.json")):
    _,_,_,tag,cond,model,_ = path.split("/")
    r = json.loads(open(path).read())
    print(f"\n[{tag} {cond} × {model}] overlap={r['number_overlap']}")
    print("  답: " + r["answer"][:220].replace("\n", " "))

print("\n" + "="*W)
print("그래프를 눈으로 보려면: http://localhost:7474 접속 → database 선택(arms1adeepseek 등)")
print(f"쿼리: MATCH (n {{_workspace_id:'arms1-a-deepseek-{cid}'}})-[r]-(m) RETURN n,r,m LIMIT 50")
PY
