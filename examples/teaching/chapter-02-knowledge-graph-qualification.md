# Chapter 2. Knowledge Graph Qualification

## Learning Objectives
- GDS 핵심 지표(node similarity, degree centrality, clustering coefficient, link prediction) 각각을 *그래프 품질 진단* 관점에서 해석한다.
- GDS in-memory projection → algorithm → write-back 한 사이클을 DozerDB에서 실행한다.
- 평가 도구를 `@function_tool`로 노출하고, agent의 도구 선택 reasoning을 Opik trace로 검증한다.

## Prerequisites
- Chapter 1 완료 (FIBO + FinDER 인덱싱된 그래프)
- DozerDB `gds.*` 권한 (CLAUDE.md §8 — `apoc.*, n10s.*` 외 별도 확인 필요)
- Opik 워크스페이스 (`tteon`) 접근

## 2.1 GDS Overview & Evaluation Metric

각 지표는 "수치가 이상하면 인덱싱 어디에 문제가 있는가" 진단 신호로 매핑한다.

| 지표 | 무엇을 보는가 | 품질 해석 / 진단 신호 |
|---|---|---|
| **Node Similarity (Jaccard)** | 이웃 집합 겹침 | 중복 엔터티 후보 → 디듀프 트리거 |
| **Degree Centrality** | 연결 수 | hub 노드 식별. 핵심 개체 degree가 낮으면 추출 누락 의심 |
| **Triangles & Clustering Coefficient** | 삼각형 closure | 도메인 응집성. FIBO처럼 분류체계 강한 도메인은 높아야 함 |
| **Link Prediction (Adamic-Adar, Common Neighbors)** | 누락 가능 엣지 | 추출이 빠뜨린 관계 후보 → 재추출 큐 |

### Hands-on
> TODO: 각 지표를 Chapter 1 그래프에 적용 → 결과 분포 히스토그램 + 이상치 top-10 표.

### Checkpoint
- degree centrality 분포가 power-law를 따르지 않으면 무엇을 의심하는가?
- node similarity 0.9 이상 페어는 자동 머지하면 안 되는 이유는?

---

## 2.2 GDS Query with Neo4j / DozerDB

### Pattern
```cypher
// 1. Project
CALL gds.graph.project('finder-fibo', ['Entity'], { MENTIONS: { orientation: 'UNDIRECTED' } })
// 2. Run
CALL gds.louvain.write('finder-fibo', { writeProperty: 'community_id' })
// 3. Drop
CALL gds.graph.drop('finder-fibo')
```

### DozerDB 주의
- `elementId(...)` 사용 (deprecated `id(...)` 금지 — CLAUDE.md §8)
- write 쿼리는 명시적 권한 필요. tool 노출 시 read-only가 기본.

### Hands-on
> TODO: 4개 지표를 각각 별도의 graph projection으로 실행하고, runtime/메모리 사용량 비교.

### Code Anchor
- `seocho/store/graph.py`

### Checkpoint
- in-memory projection이 끝나도 drop하지 않으면 어떤 문제가 발생하는가?

---

## 2.3 Tools Build

평가 도구를 `@function_tool`로 4개 노출.

### Tools
- `compute_node_similarity(label: str, top_k: int = 10) -> list[SimilarityPair]`
- `find_hub_entities(label: str, top_k: int = 10) -> list[HubEntity]`
- `detect_communities(algorithm: Literal['louvain', 'leiden']) -> CommunityReport`
- `suggest_missing_links(label: str, top_k: int = 20) -> list[LinkSuggestion]`

### 설계 원칙
- 각 도구 docstring에 **언제 호출해야 하는지** 시나리오 1~2줄 포함 → agent의 tool selection 정확도 결정 요인.
- 인자/반환 schema는 Pydantic 모델로 고정.
- 기본 read-only. write가 필요한 경우 명시적 플래그.

### Code Anchor
- `seocho/tools.py` (기존 8개 tool 패턴 참고)
- `seocho/agents.py`

### Hands-on
> TODO: 4개 도구를 직접 구현하고, 단위 테스트로 반환 schema 검증.

---

## 2.4 Agent Recognize Tools

### 시나리오
사용자: "이 그래프 품질 평가해줘"

### 관찰 포인트
- agent가 호출하는 도구 *순서*와 *조합*
- docstring이 모호한 버전 vs 명확한 버전에서 호출 패턴 차이
- 불필요한 도구 호출 (over-calling) 여부

### Hands-on
> TODO: docstring 두 버전(모호 / 명확)으로 같은 질의를 실행 → tool_use trace diff.

### Checkpoint
- agent가 도구 4개를 직렬로 호출했다. 병렬화 가능한 호출은 어느 것인가?

---

## 2.5 Action Check with Opik — Reasoning & Tool_use

### Opik trace에서 확인할 3가지
1. **Tool input/output 페어** — 입력이 docstring 스펙대로인지
2. **Reasoning step의 도구 선택 근거** — 어떤 사용자 의도가 어떤 도구로 이어졌는지
3. **비용** — 토큰 / latency / 도구 호출 횟수

### Evaluation Checklist
- [ ] 필요한 도구만 호출했는가 (over-calling 없음)
- [ ] 병렬화 가능한 호출을 병렬로 했는가
- [ ] 도구 결과를 최종 답변에 *실제로* 인용했는가 (사용하지도 않을 도구를 호출하지 않았는가)
- [ ] reasoning step이 도구 결과와 모순되지 않는가

### Hands-on
> TODO: 같은 질의를 5회 반복 실행 → Opik에서 평균 toolcall 수, 평균 latency, 답변 일관성 측정.

### Code Anchor
- `seocho/eval/` (벤치마크 하니스)

---

## Deliverables
- [ ] 4개 지표 결과 리포트 (히스토그램 + top-10)
- [ ] `@function_tool` 4개 구현 + 단위 테스트
- [ ] Opik trace 캡처 (모호 docstring vs 명확 docstring 비교)
- [ ] Evaluation checklist 결과 요약
