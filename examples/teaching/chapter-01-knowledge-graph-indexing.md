# Chapter 1. Knowledge Graph Indexing

## Learning Objectives
- Source → Chunk → Entity 3-layer LPG 구조를 설명하고, RDF triple 대비 장점을 metadata 관점에서 정당화한다.
- Ontology-aware extraction/linking 프롬프트를 설계하고, schema 강제 유무에 따른 출력 차이를 정량적으로 비교한다.
- DozerDB 위에서 community detection을 실행하고, 결과를 노드 property로 write-back한다.

## Prerequisites
- Neo4j/DozerDB 인스턴스 (`bolt://localhost:7687`), `apoc.*`, `n10s.*`, `gds.*` 권한
- `pip install seocho`
- 데이터셋: `examples/teaching/context_dataset_FinDER.pdf` (1차 인덱싱 대상), `examples/datasets/fibo_be_minimal.ttl` (스키마)

## 1.1 Source, Chunk, Entity (3-layer)

### Concept
> TODO: 왜 단일 (:Entity) 노드가 아니라 (:Source)-[:HAS_CHUNK]->(:Chunk)-[:MENTIONS]->(:Entity)로 분리하는가
> — 출처 추적성, 청크 단위 임베딩 재계산, 청크-엔터티 다대다 관계, retraction(원본 제거)의 cascade 규칙.

### Hands-on
> TODO: FinDER PDF 1건을 인덱싱하고, Cypher로 3계층 노드/엣지 카운트를 확인.
> ```cypher
> MATCH (s:Source)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(e:Entity)
> RETURN count(DISTINCT s), count(DISTINCT c), count(DISTINCT e)
> ```

### Code Anchor
- `seocho/index/pipeline.py`
- `seocho/index/ingestion_facade.py`

### Checkpoint
- 동일 회사명이 청크 5개에 등장할 때 Entity 노드 / MENTIONS 엣지는 각각 몇 개인가?
- Source를 삭제하면 어디까지 cascade되어야 하는가?

---

## 1.2 Entity Extraction & Linking — Prompt Design

### Extraction Prompt 구조
- (a) 시스템 프롬프트에 ontology class/property *발췌*만 주입 → `seocho.ontology_slice.slice_ontology(intent=...)`
- (b) JSON schema 강제 (출력 free-form 금지)
- (c) `evidence_span` 필드 의무화 — 청크 원문 인용 범위

### Linking Prompt 구조
- 임베딩 top-k 후보를 미리 추려서 LLM에 전달
- LLM은 "동일 엔터티 여부 + confidence + 근거" 만 응답, 자유생성 금지

### Hands-on
> TODO: 같은 청크에 대해 두 가지 프롬프트(A: schema 없음 / B: schema + evidence) 실행 → 추출 엔터티 수, false positive, span 정확도 비교.

### Code Anchor
- `seocho/index/linker.py`
- `seocho/ontology_slice.py`

### Checkpoint
- ontology 발췌를 전부 다 주입하지 않고 slice하는 이유는? (token budget, attention dilution)
- evidence_span을 강제하면 어떤 종류의 hallucination이 사라지는가?

---

## 1.3 Label Property Graph의 이점 — Metadata

### Concept
> TODO: LPG는 *관계*에 직접 property를 붙일 수 있다 → 추출 출처/타임스탬프/confidence/모델버전이 edge metadata.
> 같은 표현을 RDF로 하려면 reification 필요 → 쿼리 복잡도와 저장 오버헤드 비교.

### Demo Query
```cypher
MATCH (a)-[r:MENTIONS]->(b)
WHERE r.confidence > 0.8 AND r.extracted_by STARTS WITH 'gpt-4o'
RETURN a.name, b.name, r.confidence, r.extracted_at
```

### Hands-on
> TODO: 인덱싱 시 MENTIONS 엣지에 `confidence`, `extracted_by`, `extracted_at` property를 채우고, 위 쿼리로 신뢰도 필터링.

### Checkpoint
- 동일 사실을 RDF triple로 표현할 때 edge property 1개당 추가로 필요한 노드/triple 수는?

---

## 1.4 Community Detection (GDS)

### 알고리즘 선택
- **Louvain**: modularity 기반, 빠름, resolution에 따라 결과 변동
- **Leiden**: Louvain 개선판, resolution 안정성 ↑

### Pipeline
1. `gds.graph.project(...)` — in-memory projection
2. `gds.louvain.write(...)` or `gds.leiden.write(...)` — community ID write-back
3. 결과 검증: 커뮤니티 크기 분포, 대표 엔터티 추출

### Hands-on
> TODO: FIBO + FinDER 인덱싱 후 community 5~7개로 분해, 각 community의 top-degree 엔터티 3개를 라벨링.

### Code Anchor
- `seocho/store/graph.py` (Cypher 실행 어댑터)

### Checkpoint
- community ID는 *어디에* 저장하는 게 맞는가 — Entity 노드 property? 별도 (:Community) 노드?
- 신규 문서 추가 시 community 재계산 전략 (incremental vs full rebuild)

---

## Deliverables
- [ ] FinDER PDF 1건 + FIBO 스키마로 3-layer 그래프 적재 완료
- [ ] Extraction prompt A/B 비교 결과 (markdown 표)
- [ ] Community detection 결과 (community 수, 크기, 대표 엔터티) 캡처
