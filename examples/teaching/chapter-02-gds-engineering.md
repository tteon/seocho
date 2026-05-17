# Chapter 2 — Appendix: GDS Engineering — Cost, Memory, Recompute Policy

> Ch 2 본편이 *어떤* 알고리즘을 *왜* 쓰는지에 집중했다면, 이 부속 문서는 *얼마나 비싼지*와 *언제 다시 돌릴지* 의 엔지니어링 결정을 다룬다.

## 0. 왜 별도 문서로 다루는가

GDS 알고리즘은 "한 번 잘 돌면 되는" 종류가 아니다. 그래프가 커지면 **메모리·시간·재계산 정책**이 운영 비용을 결정하고, 잘못 고른 알고리즘은 노트북에선 1초인데 production에선 OOM 으로 죽는다. 학습자가 강의 이후 직접 운영할 때 가장 자주 부딪히는 결정 지점이다.

---

## 1. 알고리즘 시간복잡도 ↔ 메모리 점유

각 항목의 *그래프 크기*는 노드 수 `n`, 엣지 수 `m`, 평균 차수 `d̄ = 2m/n` 기준. GDS Java heap 점유는 in-memory projection이 차지하는 양.

| 알고리즘 | 시간 복잡도 | Java heap 추정 | 강의 사용 단계 | 비고 |
|---|---|---|---|---|
| `gds.graph.project` | `O(n + m)` | `~16·n + ~24·m` bytes (CSR) | 매 사이클 시작 | projection은 read-only sliced view |
| `gds.degree.stream` | `O(n + m)` | (projection만) | Ch 2.2 (b) | 가장 싼 지표 |
| `gds.nodeSimilarity.stream` | `O(n·d̄²)` 근방 (탑K 가정) | `~n·k·12` (heap of pairs) | Ch 2.2 (a) | k 작게 (≤ 10) 유지 |
| `gds.localClusteringCoefficient` | `O(n·d̄²)` 최악 (삼각형 counting) | 거의 projection 수준 | Ch 2.2 (c) | 별도 메모리 보조 거의 X |
| `gds.louvain` | `O(m·log n)` 평균, 라운드 수 의존 | `~32·n` workspace + community arrays | Ch 1.4 | seed parameter로 재현성 |
| `gds.leiden` | Louvain + refine 단계 | Louvain 대비 ~1.2× | (옵션) | resolution 안정성 ↑ |
| `gds.alpha.linkprediction.adamicAdar` | `O(d̄)` per pair | 추가 메모리 미미 | Ch 2.2 (d) | per-pair 호출, 페어 수 cap 필수 |

### 1.1 실용 휴리스틱
- **노드 100만 이하**: 모두 안전.
- **노드 100만~1천만**: Louvain · degree · clustering OK. nodeSimilarity 는 `topK ≤ 5`, similarityCutoff 0.7 이상 강제.
- **노드 1천만 이상**: Streaming/write 분리. 비싼 알고리즘은 community-별 sharding 후 부분 실행.

---

## 2. Projection 메모리 추정 공식

> *"Projection은 그래프 자체보다 더 크다."*

GDS 메모리 추정 procedure를 사용하면 실제 실행 전에 검증 가능.

```cypher
CALL gds.graph.project.estimate(
  ['Entity'],
  {MENTIONS: {orientation: 'UNDIRECTED'}}
) YIELD bytesMin, bytesMax, requiredMemory
RETURN bytesMin, bytesMax, requiredMemory;
```

수동 추정 식 (대략적, CSR 가정):

```
projection_bytes ≈
    24·n           # node ID + label hash
  + 24·m           # CSR offsets
  + 8 ·m·P_e       # P_e: edge property 개수
  + 4 ·n·P_n       # P_n: node property 개수
```

**예시**: 노드 5만 · 엣지 30만 · 엣지 property 0 · 노드 property 2.
```
24·50_000 + 24·300_000 + 0 + 4·50_000·2
= 1.2M + 7.2M + 0 + 0.4M
≈ 8.8 MB
```
이걸 알고리즘 workspace (Louvain은 +`32·n` ≈ 1.6MB) 와 합쳐 heap 여유와 비교.

---

## 3. 증분 vs 풀 재계산 정책

신규 문서가 추가될 때마다 모든 GDS 지표를 다시 돌리면 비용 폭증. 다음 표에 따라 트리거를 정한다.

| 지표 | 권장 정책 | 트리거 기준 |
|---|---|---|
| `degree` | **풀 재계산**, 단 매우 싸므로 incremental 필요 없음 | 야간 배치 또는 노드 +5% 시 |
| `community_id` (Louvain) | **풀 재계산** | 노드 +10% 또는 엣지 +20% 또는 도메인 변화 신호 |
| `nodeSimilarity` | **부분** — 새 노드만 candidates로 | 노드 추가마다 (실시간) |
| `clusteringCoefficient` | **풀** but throttled | 주 1회 야간 |
| `linkprediction` | **on-demand** — agent 호출 시점에 페어별 계산 | per query |

### 3.1 시그널 기반 트리거
다음 조건을 GDS write-back 결과에 같이 저장해 "다음 재계산이 언제 필요한가"를 평가한다.

```cypher
// Louvain 재계산 직후 modularity를 별도 노드에 기록
MERGE (m:GDSRunMeta {algo: 'louvain', workspace_id: $ws})
SET m.last_run_at = datetime(),
    m.modularity = $modularity,
    m.community_count = $count,
    m.node_count_at_run = $nodes,
    m.edge_count_at_run = $edges;
```

이후 *변화량*이 임계값을 넘으면 재계산:
```cypher
MATCH (m:GDSRunMeta {algo: 'louvain', workspace_id: $ws})
MATCH (n) WHERE n.workspace_id = $ws RETURN m.node_count_at_run AS prev, count(n) AS now
// (now - prev) / prev > 0.10 → trigger
```

---

## 4. 안전 운영 패턴

### 4.1 사용 후 즉시 drop
```python
def gds_run_session(graph_name: str, projection_cypher: str, algo_calls: list[str]):
    try:
        gds_run(f"CALL gds.graph.drop('{graph_name}', false)")  # idempotent cleanup
        gds_run(projection_cypher)
        results = [gds_run(call) for call in algo_calls]
        return results
    finally:
        gds_run(f"CALL gds.graph.drop('{graph_name}')")
```

### 4.2 elementId() 마이그레이션
CLAUDE.md §8 — deprecated `id()` 사용 금지. GDS 결과에서 노드 식별 시 `gds.util.asNode(nodeId).<property>` 또는 직접 elementId 매핑:

```cypher
CALL gds.degree.stream('graph')
YIELD nodeId, score
WITH gds.util.asNode(nodeId) AS node, score
RETURN elementId(node) AS eid, node.name AS name, score
LIMIT 10
```

### 4.3 Write-back 쿼리는 멱등성 보장
같은 algorithm을 같은 graph에 두 번 돌려도 결과가 같아야 한다 (`seed`, `tolerance` 등 명시).

```cypher
CALL gds.louvain.write('graph', {
  writeProperty: 'community_id',
  randomSeed: 42,
  tolerance: 0.0001,
  maxIterations: 10
})
```

---

## 5. DozerDB 호환성 메모

- DozerDB의 GDS 노출 집합은 Neo4j Enterprise 의 부분집합이다. 강의에서 쓰는 5종 (`degree`, `louvain`, `nodeSimilarity`, `localClusteringCoefficient`, `alpha.linkprediction.adamicAdar`) 은 모두 검증됨.
- `gds.alpha.*` 네임스페이스는 GDS 버전에 따라 안정 namespace로 이동할 수 있음. 노트북에서 try/except로 namespace 변경에 대비.
- `gds.graph.project.cypher` (강의에서 사용) 는 GDS 2.x 에서 deprecated 경고가 뜨지만 DozerDB 빌드에서는 여전히 동작. 안정 alternative는 native projection.

---

## 6. 운영 체크리스트

지표 1개를 운영에 올리기 전 확인:

- [ ] `gds.graph.project.estimate` 로 메모리 산정 → heap 의 30% 이하인가?
- [ ] write-back property 이름이 다른 알고리즘과 충돌하지 않는가?
- [ ] 재계산 트리거 기준 (노드/엣지 증가율) 이 문서화되어 있는가?
- [ ] 알고리즘 결과에 `GDSRunMeta` 메타 노드를 함께 적재하는가?
- [ ] projection drop이 finally block 또는 context manager로 보장되는가?

---

## 7. SEOCHO SDK 표준화 후보

현재 강의 노트북은 매번 `gds.graph.project.cypher(...)` Cypher를 raw로 작성. SDK 측에서 안전 운영 패턴을 한 번에 노출하면 좋다:

```python
from seocho.gds import gds_session, MetricSpec

with gds_session(graph_store, graph_name='ch02-quality') as g:
    g.project_entities(relationship='MENTIONS')
    sim = g.metric(MetricSpec.NODE_SIMILARITY, top_k=10)
    deg = g.metric(MetricSpec.DEGREE)
    g.louvain(write_property='community_id', seed=42)
    # exiting drops the projection + writes GDSRunMeta automatically
```

→ bd 티켓: **"seocho.gds — safe GDS session helper with projection lifecycle + run metadata"** 후보.

---

## 8. 한 줄 요약

> *"GDS 결과는 그래프의 진실이 아니라 그래프의 *지금 시점 추정*이다. 비싸지만 cache하고, 변화량 트리거로만 갱신한다."*
