# Chapter 4. Routing Agent

## Learning Objectives
- 질의를 4축(intent / entity / topic / rewriting)으로 augment하고, 각 축의 결과로 검색 백엔드를 라우팅한다.
- vector / fulltext / Cypher 3개 백엔드를 병렬 호출하고, score scale을 정규화하여 RRF로 통합한다.
- 인용 의무와 거절 계약(refusal contract)을 강제하여 fabrication을 차단한다.

## Prerequisites
- Chapter 1~3 완료
- FIBO + FinDER 인덱싱된 그래프 + community ID write-back 완료

## 4.1 Question Augmentation

### 4-축 augmentation
각 augmentation은 *별도 호출*로 분리해 trace에서 추적 가능하게 한다.

#### Axis 1. Intent Classification
- 카테고리: `lookup` / `aggregation` / `comparison` / `explanation`
- 분류 결과로 §4.2 백엔드 가중치 결정

#### Axis 2. Entity Extraction
- 질의 속 명명 개체를 ontology class로 lift
- Chapter 1.2의 linking 프롬프트 재사용 가능

#### Axis 3. Topic Mapping
- Chapter 1.4에서 write-back한 `community_id` 와 매칭
- 검색 범위(scope)를 community 1~2개로 좁히기

#### Axis 4. Query Rewriting
- 대명사/줄임말 해소
- multi-hop 질의는 sub-query 리스트로 분해

### Hands-on
> TODO: 같은 질의에 대해 4-axis augmentation 결과를 JSON으로 dump → 라우팅 결정에 어떻게 쓰이는지 추적.

### Code Anchor
- `seocho/agents.py`
- `seocho/session.py` (entity cache, query cache)

### Checkpoint
- 4축을 1번의 LLM 호출에 묶으면 어떤 장점/단점이 있는가? (token 절약 vs trace 분해 어려움)

---

## 4.2 Select Proper Context from Parallel Search

### 3-Backend
- **Vector** — semantic similarity, explanation류 질의에 강함
- **Fulltext (BM25)** — 정확 키워드, 법령/조항 류에 강함
- **Cypher pattern match** — 구조화된 lookup/aggregation

### Routing Table

| Intent | Vector | Fulltext | Cypher | 비고 |
|---|---|---|---|---|
| `lookup` (entity 식별됨) | low | low | **high** | Cypher 단독으로 충분한 경우 많음 |
| `lookup` (entity 모호) | mid | **high** | mid | fulltext로 후보 좁힌 후 Cypher 재호출 |
| `aggregation` | low | low | **high** | GDS 도구도 후보 |
| `comparison` | **high** | **high** | **high** | 3개 모두 + rerank |
| `explanation` | **high** | mid | low | vector + 보조 fulltext |

### Hands-on
> TODO: 5종 intent에 대해 라우팅 결정 + 백엔드 응답 비교. 라우팅 테이블을 직접 채워보는 과제.

### Checkpoint
- Cypher 결과가 비었을 때 fallback 정책은? (다른 백엔드로 자동 폴백 vs 사용자에게 명시적 알림)

---

## 4.3 Context Aggregation

### Pipeline
1. **Dedup**: `(source_id, chunk_id)` 기준 중복 제거
2. **Score normalization**: 백엔드별 score scale이 다르므로 min-max 또는 z-score 정규화
3. **Fusion**: Reciprocal Rank Fusion (RRF)
   ```
   RRF_score(d) = sum over backends b of 1 / (k + rank_b(d))
   ```
   k는 보통 60.
4. **Top-N 절단**: context window에 맞춰 top 5~10개

### 흔한 함정
- vector cosine과 BM25 score를 *그대로 더하기* → scale 차이로 한쪽이 dominant
- 같은 chunk가 다른 백엔드에서 다른 score로 반환됐을 때 max를 쓸지 sum을 쓸지 정책 결정 필요

### Hands-on
> TODO: 단순 sum vs RRF 비교 → top-5 context의 의미적 다양성(distinct community 수)으로 평가.

### Code Anchor
- `seocho/store/vector.py`
- `seocho/store/graph.py`

### Checkpoint
- RRF가 score scale에 robust한 이유는? (rank만 쓰기 때문)

---

## 4.4 Answer Generation

### 인용 의무
- 각 사실 문장 끝에 `[source_id:chunk_id]` 형식 인용 강제
- 인용이 없는 문장은 답변에서 제거 (post-validation)

### 거절 계약 (Refusal Contract)
- 컨텍스트에 근거 없으면 fabrication 금지
- 표준 거절 응답:
  > "제공된 컨텍스트에는 이 질문에 답할 근거가 없습니다. 다음을 시도해보세요: [구체적 후속 행동]"
- 거절도 trace로 기록 (CLAUDE.md §18 refusal contract)

### Output Envelope
```
<answer>...본문 with [src:chunk] 인용...</answer>
<citations>
  - src:abc, chunk:12 — "원문 인용 span"
  - src:def, chunk:5  — "원문 인용 span"
</citations>
<confidence>high | medium | low</confidence>
```

### Hands-on
> TODO: 같은 질의에 대해 (a) 인용 의무 없음 / (b) 인용 의무 강제 답변 비교 → fabrication 비율 측정.

### Checkpoint
- 인용된 chunk와 답변 문장이 의미적으로 일치하는지를 어떻게 *자동* 검증하는가? (entailment 모델 또는 NLI)

---

## Deliverables
- [ ] 4-axis augmentation 결과 JSON 샘플 5개
- [ ] 라우팅 테이블 정의 + intent별 검증
- [ ] RRF aggregation 구현 + 평가 결과
- [ ] 인용 강제 답변 envelope 샘플 + fabrication 비율 표
