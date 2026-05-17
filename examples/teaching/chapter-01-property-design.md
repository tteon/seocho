# Chapter 1 — Appendix: Node & Relationship Property Design

> Ch 1 §1.3 "LPG Metadata Advantage" 확장. 각 노드/엣지에 *어떤* property를 *왜* 두는지 체계적으로 설계한다.

## 0. 설계 원칙

property를 추가하기 전에 항상 다음을 자문한다:

| 질문 | 답이 yes면 |
|---|---|
| 이 property 없이 어떤 쿼리가 *실행 불가*인가? | 추가 정당화 ✓ |
| 인덱싱 시점에 결정되는가 (불변)? | **노드 property** 후보 |
| 매 추출/관찰마다 다른가 (가변)? | **엣지 property** 후보 |
| 인덱싱 시점엔 모르고 사후 계산되는가? | denormalized property (community_id, degree) |
| 외부 시스템과의 동기화가 필요한가? | `external_ids` 맵 |

원칙: **불변은 노드, 가변·증거는 엣지.** 같은 entity가 여러 문서에 등장하면 confidence는 *문서마다 다르다* → MENTIONS edge에 둠.

---

## 1. `(:Source)` — 원본 문서 layer

```cypher
(:Source {
  source_id: "<sha256 or UUID>",       // primary key
  uri:       "s3://bucket/finder/A.pdf",
  mime_type: "application/pdf",
  title:     "Apple Inc. 10-K 2024",
  author:    "Apple Inc.",
  published_at: datetime("2024-09-30"),
  language:  "en",
  category:  "Risk",                    // FinDER 카테고리 등 도메인 라벨
  workspace_id: "teaching-ch01-hardy",
  ingested_at:  datetime(),
  ingested_by:  "agent:indexer-v3",
  checksum:   "sha256:...",
  version:    1,                        // 재인덱싱 카운터
  parent_source_id: null,               // derived(요약·번역 등) 시 부모 참조
  tags:       ["10-K", "FY2024", "sp500"]
})
```

**왜 이 set인가**
- `source_id` + `checksum`: 같은 파일이 재업로드되면 노드 1개 유지 (idempotent ingest).
- `parent_source_id`: 번역본·요약본·OCR 결과 등 derived 인공물의 lineage.
- `workspace_id`: 멀티 테넌시 (CLAUDE.md §6.1) — 모든 노드에서 강제.
- `version`: 재인덱싱(스키마 변경 등) 시 이전 버전과 구분.
- `tags`: 자유 라벨이지만 enum화하면 search rerank에 활용.

**적용 쿼리 예시**
```cypher
// 같은 회사의 가장 최근 10-K만
MATCH (s:Source {workspace_id: $ws})
WHERE s.author = "Apple Inc." AND "10-K" IN s.tags
RETURN s ORDER BY s.published_at DESC LIMIT 1
```

---

## 2. `(:Chunk)` — 청크 layer

```cypher
(:Chunk {
  chunk_id:     "<source_id>:<ordinal>",
  ordinal:      0,                        // 0부터 시작
  text:         "...the chunk content...",
  char_start:   1024,                     // 원본에서의 offset
  char_end:     2048,
  token_count:  287,
  chunker:      "recursive",              // fixed | semantic | recursive | sliding
  chunker_version: "v3.1",
  embedding_model: "text-embedding-3-large",
  embedding_vector_id: "vec://lancedb/...", // 외부 vector store 참조
  quality_score: 0.87,                    // 추출 confidence 집계
  language:     "en"
})
```

**왜 이 set인가**
- `char_start`/`char_end`: evidence_span 검증과 UI 하이라이팅에 필수.
- `chunker_version`: 청크 경계 알고리즘이 바뀌면 임베딩 재계산. 버전 비교로 재처리 대상 식별.
- `embedding_vector_id`: 임베딩 *값*은 벡터 DB에. 그래프엔 식별자만 → 그래프 사이즈 폭증 방지.
- `quality_score`: 청크의 추출 신뢰도 평균. 라우팅 시 high-quality 청크 우선.

**Anti-pattern**: `embedding` (실제 float 배열)을 노드에 직접 저장. 그래프 디스크 폭증 + 빠른 GDS 투영 방해.

---

## 3. `(:Entity)` — 엔터티 layer

```cypher
(:Company {  // 또는 :Risk, :Filing, :Executive 등 ontology class
  entity_id:    "fibo:company:apple-inc",
  name:         "Apple Inc.",
  aliases:      ["Apple", "AAPL", "애플"],
  class:        "Company",
  description:  "Multinational technology company...",
  first_seen_at: datetime("2024-09-30"),
  last_seen_at:  datetime("2026-05-16"),
  mention_count: 142,                     // denormalized
  community_id:  3,                       // Ch 1.4 Louvain
  degree:        47,                      // denormalized centrality
  external_ids: {                         // 외부 ID 맵
    "wikidata": "Q312",
    "lei":      "HWUPKR0MPOU8FGXBT394",
    "cik":      "0000320193"
  },
  validated:    true,                     // 거버넌스 승인 여부
  validated_by: "ontology-validator-v2",
  validated_at: datetime()
})
```

**왜 이 set인가**
- `entity_id` (`fibo:company:apple-inc` 형식): 결정적 키 — 같은 entity가 여러 문서에 등장해도 dedup 가능.
- `aliases`: 약자/한국어/표기 변형. text2cypher가 `$name in e.aliases` 패턴으로 활용.
- `external_ids`: Wikidata/LEI/CIK 등 정규 식별자. 외부 데이터와 조인할 때 핵심.
- `mention_count` / `degree`: GDS 매번 돌리지 않게 denormalized — incremental update 필요.
- `community_id`: Ch 4 라우팅에서 검색 범위 좁히기에 사용.
- `validated`: ontology governance에서 승인된 노드만 production query에 노출.

**주의**: `external_ids`는 *맵*. Neo4j 5.x는 map property 지원. 4.x 사용 시 별도 `(:ExternalId)` 노드로 분리.

---

## 4. `[:HAS_CHUNK]` — Source → Chunk

```cypher
(:Source)-[:HAS_CHUNK {
  ordinal:    0,
  created_at: datetime()
}]->(:Chunk)
```

**왜 미니멀한가**: HAS_CHUNK는 *결정적* 컨테이너 관계. provenance·trust는 MENTIONS edge에서. 여기에 confidence를 두면 의미가 모호해짐.

---

## 5. `[:MENTIONS]` — Chunk → Entity (가장 정보 밀집된 엣지)

```cypher
(:Chunk)-[:MENTIONS {
  evidence_span:  "Apple Inc. (the \"Company\") is a...",
  char_start:     45,                     // 청크 내 offset
  char_end:       97,
  confidence:     0.93,
  extracted_by:   "gpt-4o-mini-2024-07-18",
  extracted_at:   datetime(),
  extraction_run_id: "run-2026-05-16T10:00Z-abc",
  prompt_version: "extract-v7",            // 프롬프트 해시
  ontology_slice_hash: "sha256:...",       // 어떤 slice를 썼는지
  agreed_by:      ["gpt-4o-mini", "kimi-k2.5", "deepseek-chat"],  // Ch 5 debate
  role:           "subject"                // subject | object | mention
}]->(:Entity)
```

**왜 이 set인가** — 각 property가 막아주는 실패 모드:

| Property | 차단하는 실패 |
|---|---|
| `evidence_span` + offsets | hallucination (Ch 1.2) |
| `confidence` | 저신뢰 데이터를 모든 쿼리가 동등 취급 |
| `extracted_by` + `prompt_version` | "왜 이때만 잘못 추출됐는가" 재현 불가 |
| `extraction_run_id` | 잘못된 run의 결과만 골라 retract |
| `ontology_slice_hash` | ontology 수정 후 영향 받는 추출 식별 |
| `agreed_by` | 단일 모델 환각 vs 다중 모델 합의 구분 |
| `role` | "주체로 언급" vs "객체로 언급" 의미 분리 |

**적용 쿼리 — \"신뢰도 0.8 이상 + 3개 모델 합의\" 만 라우팅에 사용**
```cypher
MATCH (c:Chunk)-[m:MENTIONS]->(e)
WHERE m.confidence > 0.8 AND size(m.agreed_by) >= 3
RETURN e, count(*) AS support
ORDER BY support DESC LIMIT 10
```

---

## 6. `[:RELATED_TO]` — Entity → Entity (ontology-driven)

```cypher
// 예: (:Company)-[:HAS_RISK]->(:Risk)
(:Entity)-[:HAS_RISK {
  confidence:     0.88,
  evidence_chunks: ["src-A:0", "src-A:5", "src-B:12"],  // 다중 근거
  extracted_by:   "ensemble",
  extracted_at:   datetime(),
  validated_by:   "rules:HAS_RISK_v3",      // SHACL-like rule 통과 여부
  temporal_range: { from: "2024-01-01", to: null },     // 시점 정보
  weight:         3.7                       // graph algorithm용
}]->(:Risk)
```

**왜 이 set인가**
- `evidence_chunks` (배열): 같은 관계가 여러 청크에서 지지되면 신뢰도 ↑. 단일 청크 근거 vs 다중 근거 구분.
- `validated_by`: SHACL-like rules engine 통과 여부 (CLAUDE.md §6.2 `/rules/validate`).
- `temporal_range`: 회사·임원·소유관계 등 시간에 따라 바뀌는 사실.
- `weight`: GDS 알고리즘이 정량 weight를 받을 때 (centrality, shortest path).

---

## 7. 카테고리별 정리

목적별로 property를 묶으면 *어떤 query를 안전하게 쓸 수 있는지* 한눈에 보인다.

| 카테고리 | Source | Chunk | Entity | MENTIONS | RELATED_TO |
|---|---|---|---|---|---|
| **Identity** | source_id, uri, checksum | chunk_id, ordinal | entity_id, name, aliases | — | — |
| **Provenance** | ingested_by, parent_source_id | chunker, chunker_version | first_seen_at, last_seen_at | extracted_by, extraction_run_id, prompt_version | extracted_by, evidence_chunks |
| **Trust** | — | quality_score | confidence, validated | confidence, agreed_by | confidence, validated_by |
| **Lineage** | parent_source_id | char_start/end | provenance(=source list) | evidence_span, char_start/end | evidence_chunks |
| **Temporal** | published_at, ingested_at, version | — | first_seen_at, last_seen_at | extracted_at | temporal_range, extracted_at |
| **Tenancy** | workspace_id | (inherits) | (inherits) | — | — |
| **Performance** | — | embedding_vector_id | mention_count, degree, community_id | — | weight |
| **External link** | tags | language | external_ids | — | — |
| **Domain** | category, title | language | class, description | role | (ontology relation name) |

---

## 8. 강의용 미니 워크북 (Ch 1 §1.3 직후 실습용)

각 학습자에게 다음 6가지 쿼리를 작성하게 한다:

1. **신뢰도 필터**: confidence > 0.8 인 entity만.
2. **다중 합의**: agreed_by 에 3개 이상 모델이 있는 MENTIONS.
3. **임시 retract**: 특정 extraction_run_id 의 MENTIONS 전체를 view에서 숨기기.
4. **시점 view**: 2024년 1월 기준 유효했던 HAS_RISK 관계만.
5. **외부 ID 조인**: wikidata QID로 외부 데이터셋과 join.
6. **community drift**: community_id 가 바뀐 entity 추적 (denormalized → 재계산 빈도 결정).

---

## 9. SEOCHO SDK 표준화 후보 (engineering improvement)

현재 SEOCHO 인덱싱이 MENTIONS edge에 자동 주입하는 metadata와 *위 표준 권고*의 갭:

- ✅ 이미 있는 것: `confidence`, `extracted_by`, `extracted_at` (대체로)
- 🔧 부분적: `evidence_span` (강제 X, prompt에 의존)
- ⚠️ 없는 것: `extraction_run_id`, `prompt_version`, `ontology_slice_hash`, `agreed_by`
- ⚠️ Source 노드의 `parent_source_id` / `workspace_id` propagation 일관성 검증 필요

→ bd 티켓: **"Standardize indexing metadata schema (provenance + trust + lineage)"** 으로 등록.

---

## 10. Temporal Sanity Check 패턴

Temporal property (`ingested_at`, `extracted_at`, `published_at`, `first_seen_at`, `last_seen_at`, `temporal_range`, `version`) 는 데이터 *신선도·시점 일관성*의 1차 신호다. 깨진 시점은 곧 깨진 결론으로 이어지므로, 라우팅 전에 반드시 sanity check를 통과시킨다.

### 10.1 7가지 검증 + 처방

| Check | 무엇을 보는가 | 처방 |
|---|---|---|
| **Future-dated provenance** | `extracted_at > now()` | clock skew / 가짜 timestamp → 해당 run id로 일괄 retract |
| **Negative temporal_range** | `valid_from > valid_until` | 추출기 버그 → 해당 edge 무효화 |
| **Overlapping facts** | 같은 (subject, predicate)에 valid_range가 겹치는 RELATED_TO 다수 | LLM 충돌 → debate(Ch 5)에서 모더레이션 |
| **Stale entities** | `last_seen_at < now() - 365d` AND mention_count > 0 | 도큐먼트 라이프사이클 끝 → archive 후보 |
| **Source freshness** | Source.published_at < query_time - SLA | 라우팅 시 confidence 페널티 |
| **Version monotonic** | 같은 source_id 의 version 이 단조 증가하지 않음 | 동시 인덱싱 race → unique constraint 추가 |
| **Run integrity** | extraction_run_id 가 일부 MENTIONS에만 존재 | 부분 실패한 run → 보완 인덱싱 또는 해당 run 전체 retract |

### 10.2 Cypher 검증 모음

```cypher
-- 1) Future-dated extraction
MATCH (c:Chunk)-[r:MENTIONS]->()
WHERE r.extracted_at > datetime()
RETURN count(*) AS future_dated_mentions;

-- 2) Inverted temporal_range
MATCH ()-[r:RELATED_TO]->()
WHERE r.temporal_range IS NOT NULL
  AND r.temporal_range.from IS NOT NULL
  AND r.temporal_range.to   IS NOT NULL
  AND r.temporal_range.from > r.temporal_range.to
RETURN count(*) AS inverted_ranges;

-- 3) Overlapping conflicting facts
MATCH (a)-[r1:RELATED_TO]->(b), (a)-[r2:RELATED_TO]->(b)
WHERE id(r1) < id(r2)
  AND type(r1) = type(r2)
  AND r1.temporal_range.from < r2.temporal_range.to
  AND r2.temporal_range.from < r1.temporal_range.to
RETURN a.name, b.name, type(r1) AS rel, count(*) AS overlaps;

-- 4) Stale entities
MATCH (e)
WHERE e.last_seen_at IS NOT NULL
  AND duration.between(e.last_seen_at, datetime()).days > 365
  AND coalesce(e.mention_count, 0) > 0
RETURN e.name, e.last_seen_at, e.mention_count
ORDER BY e.last_seen_at ASC LIMIT 20;

-- 5) Run integrity — orphan MENTIONS without run id
MATCH ()-[r:MENTIONS]->()
WHERE r.extraction_run_id IS NULL OR r.extraction_run_id = ''
RETURN count(*) AS orphan_extractions;
```

### 10.3 권고 검증 정책

- 라우팅(Ch 4)에서 `confidence` 점수에 *temporal staleness penalty*를 곱한다:
  `effective_confidence = confidence * exp(-Δt / τ)` where τ = 도메인별 half-life.
- 인덱싱 종료 직후 위 5개 쿼리를 자동 실행 → 위반 0 검증 통과해야 production routing에 노출.
- 위반 발생 시 즉시 Opik trace에 `temporal_anomaly` tag로 기록 (워크스페이스 단위 알림).

### 10.4 SEOCHO SDK 표준화 후보

현재는 학습자가 매번 Cypher를 새로 작성해야 함. SDK에 다음을 노출하면 좋다:

```python
from seocho.index.sanity import run_temporal_checks
report = run_temporal_checks(graph_store, workspace_id=ws)
# → {'future_dated_mentions': 0, 'inverted_ranges': 0, ...}
report.assert_clean()  # raises TemporalAnomalyError if any > 0
```

→ bd 티켓: **"Temporal sanity check utility for indexed graphs"** 로 등록 (Ch 1 부속 문서 §10에서 정의된 5개 검증을 표준화).

---

## 11. 한 줄 요약

> *"property를 추가할 때마다 '없으면 어떤 쿼리가 깨지는지'를 적어둬라. 깨지는 쿼리가 없으면 그 property는 fluff."*
