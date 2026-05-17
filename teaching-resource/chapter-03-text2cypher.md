# Chapter 3. Text2Cypher

## Learning Objectives
- 3-블록 프롬프트(ontology 발췌 + few-shot + 출력 제약)로 Cypher 생성 정확도를 baseline 대비 측정 가능하게 끌어올린다.
- 흔한 실패 패턴(라벨 hallucination, property 오타, 양방향 누락)을 식별하고 프롬프트 수정으로 차단한다.
- Ontology TTL 수정(특히 한국어 label/synonym 추가)이 자연어 질의 정확도에 미치는 영향을 정량적으로 검증한다.

## Prerequisites
- Chapter 1~2 완료
- FIBO TTL 편집 가능한 환경 (`examples/datasets/fibo_be_minimal.ttl`)

## 3.1 Prompt Design

### 3-블록 구조

#### Block 1. Ontology 발췌
- 전체 ontology 주입 금지 — `seocho.ontology_slice.slice_ontology(intent=...)` 로 관련 class/property만 선별
- 형식: 클래스/관계/속성을 표 또는 간결한 자연어 카드로

#### Block 2. Few-shot 예제 (3개)
- **Easy**: 단일 패턴 매칭 (`MATCH (c:Company {name: $name}) RETURN c`)
- **Medium**: 1-hop join + 필터
- **Hard**: 집계 + 정렬 + LIMIT

#### Block 3. 출력 제약
- READ-only (`CREATE`, `MERGE`, `DELETE`, `SET` 금지)
- `elementId(...)` 사용, deprecated `id(...)` 금지
- 모든 쿼리에 `LIMIT` 강제 (기본값 25)
- 출력은 Cypher 단일 블록, 설명/주석 금지

### Hands-on
> TODO: 같은 질의 10개에 대해 3-블록 프롬프트 적용 전/후 정확도(executable + 결과 정확) 비교.

### Code Anchor
- `seocho/ontology_slice.py` — ontology 발췌
- `seocho/query/` (text2cypher 진입점)

---

## 3.2 Failure Patterns

| 패턴 | 증상 | 프롬프트 처방 |
|---|---|---|
| **Label hallucination** | 존재하지 않는 `:CompanyType` 라벨 사용 | Block 1에 *허용 라벨 목록* 명시 + "이 외 라벨 금지" |
| **Property 오타** | `c.companyName` vs `c.company_name` | Block 1에 property name을 정확히 인용 |
| **양방향 관계 누락** | `(a)-[:OWNS]->(b)` 만, 역방향 무시 | Block 1에 관계의 방향성 의미 명시 |
| **LIMIT 누락** | 대량 결과 반환으로 OOM | Block 3 출력 제약 + post-validation |
| **Cypher injection** | 사용자 입력이 라벨/property로 보간 | 동적 라벨은 화이트리스트 검증 후만 허용 (CLAUDE.md §8) |

### Hands-on
> TODO: 각 실패 패턴을 재현하는 질의 1개씩 → 프롬프트 처방 적용 → 차단 확인.

### Checkpoint
- LLM이 `MATCH (c:Company)-[:OWNS*]->(b)` 처럼 unbounded path를 생성했다. 어떻게 막을 것인가?

---

## 3.3 Ontology .ttl Modification

### 왜 ttl을 수정하는가
Text2Cypher 정확도는 ontology의 *언어적 풍부도*에 비례한다.
- 영문 `rdfs:label`만 있는 ontology에 대해 한국어 질의 → 매칭 실패
- 동의어/축약어(synonym)가 없으면 자연어 변형에 취약

### 수정 패턴
```turtle
fibo-be:Company
    rdfs:label "Company"@en ,
               "회사"@ko ,
               "기업"@ko ;
    skos:altLabel "Corporation"@en ,
                  "법인"@ko ;
    rdfs:comment "A legal entity..."@en ,
                 "법인격을 갖는 사업체..."@ko .
```

### Hands-on
> TODO: FIBO TTL 발췌 5개 클래스에 한국어 label + synonym 추가 → 같은 한국어 질의 10개에 대해 수정 전/후 Cypher 생성 정확도 비교.

### Code Anchor
- `examples/datasets/fibo_be_minimal.ttl`
- `seocho/ontology_serialization.py`

### Checkpoint
- TTL 수정 후 ontology 재로드 시 기존 그래프 인스턴스에 영향이 있는가? (정답: 없음. ontology는 governance plane, 인스턴스는 data plane)
- skos:altLabel과 rdfs:label의 prompt 주입 우선순위는?

---

## Deliverables
- [ ] 3-블록 프롬프트 템플릿 1개
- [ ] 10-question evaluation set (질문 + 정답 Cypher + 정답 결과)
- [ ] TTL 수정 전/후 정확도 표
- [ ] 실패 패턴 5종 차단 검증
