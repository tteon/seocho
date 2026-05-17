# Chapter 3 — Appendix: Cypher Failure Taxonomy (12 patterns)

> Ch 3 본편이 5종 실패 패턴을 다뤘다면 이 부속 문서는 그 목록을 *12종*으로 확장하고, **자동 검출 방법** + **provider별 빈도 매트릭스** + **방어 우선순위**까지 정리한다.

## 0. 분류 원칙

12개 패턴을 *발생 단계*별 3그룹으로 분류한다.

| 그룹 | 단계 | 패턴 # |
|---|---|---|
| **Generation** | LLM이 만든 시점 | 1~5 (구조적 오류) |
| **Execution** | DB가 실행할 때 | 6~9 (의미적 오류) |
| **Semantics** | 결과는 나오지만 의미가 틀림 | 10~12 (silent failure) |

silent failure (10~12)가 가장 위험. executable rate만 측정하면 놓친다.

---

## 1. Generation-time Failures (Cypher 생성 단계)

### #1 Label hallucination
**증상**: 존재하지 않는 라벨 (`PrivateCompany`, `CorporateEntity`) 사용.
**검출** (regex):
```python
ALLOWED_LABELS = {'Company', 'Risk', 'Filing', 'Executive', 'Source', 'Chunk', 'Entity'}
RE = re.compile(r':(\w+)(?:\s*[{(])')
def detect_label_hallucination(cypher: str):
    return [lbl for lbl in RE.findall(cypher) if lbl not in ALLOWED_LABELS]
```
**처방**: prompt Block 1에 *허용 라벨 목록* + "이외 금지" 명시.

### #2 Property typo / camelCase confusion
**증상**: `c.companyName` vs `c.company_name`.
**검출**: AST 파싱이 정확하지만 비싸므로 통상 schema 정합성 체크로 갈음.
```python
ALLOWED_PROPS = {'Company': {'name', 'lei', 'cik'}, 'Risk': {'name', 'severity'}}
RE = re.compile(r'(\w+)\.(\w+)')  # var.prop
```
**처방**: prompt Block 1에 property 이름을 verbatim 인용. 모델 마다 snake/camel 선호 다름.

### #3 양방향 관계 누락
**증상**: `(a)-[:OWNS]->(b)` 만 작성, `<-` 역방향 무시.
**검출**: 관계 방향이 ontology에서 mutable 표시인지 확인. ontology에 `bidirectional: true` 메타가 있으면 양방향 매칭 강제.
**처방**: 해당 관계에 대해 prompt에 `(a)-[:REL]-(b)` (방향 X) 예시 포함.

### #4 LIMIT 누락
**증상**: `RETURN n` 으로 끝나는 query.
**검출**:
```python
def missing_limit(cypher: str) -> bool:
    body = cypher.strip().rstrip(';').strip()
    return not re.search(r'\bLIMIT\s+\d+\b', body, re.IGNORECASE) \
           and 'RETURN' in body.upper()
```
**처방**: post-validation에서 LIMIT 25 자동 추가.

### #5 Cypher injection
**증상**: 사용자 입력이 라벨/property로 보간되어 destructive op 포함.
**검출**:
```python
DESTRUCTIVE = re.compile(r'\b(CREATE|MERGE|DELETE|SET|DETACH|REMOVE|DROP)\b', re.IGNORECASE)
def has_write_op(cypher: str) -> bool: return bool(DESTRUCTIVE.search(cypher))
```
**처방**: 동적 라벨은 화이트리스트 검증 후만 허용 (CLAUDE.md §8). 백엔드의 read-only session도 2차 방어.

---

## 2. Execution-time Failures (DB가 실행할 때)

### #6 Unbounded path expansion
**증상**: `(a)-[*]->(b)` 처럼 변수 길이 제한 없는 path → graph OOM.
**검출**:
```python
RE = re.compile(r'\[[^\]]*\*\s*\.\.\s*\]|\[[^\]]*\*\s*\]')
def unbounded_path(cypher: str) -> bool: return bool(RE.search(cypher))
```
**처방**: `*1..3` 같은 상한 강제. prompt에 max-hop 명시.

### #7 Cartesian product
**증상**: `MATCH (a:Company), (b:Risk) RETURN a, b` — 명시적 join 없는 다중 MATCH.
**검출**: AST 파싱 필요. 휴리스틱은 같은 MATCH 절에 콤마로 패턴이 2개 이상 + WHERE 절에 양쪽을 연결하는 술어가 없는 경우.
**처방**: prompt few-shot에 단일 MATCH로 chained pattern 만 사용.

### #8 Null property crash
**증상**: `WHERE c.published_at > date('2024-01-01')` — null property 비교에서 silent false (Cypher는 null 비교가 null).
**검출**: 정적 검증 어려움 — runtime metric (NULL ratio per property) 으로 모니터링.
**처방**: `IS NOT NULL` 명시 또는 `coalesce(...)` 강제.

### #9 Read-only violation
**증상**: read-only session에서 write op 시도.
**검출**: `has_write_op()` (#5 와 동일) 으로 사전 차단.
**처방**: read-mode 백엔드는 write op 받으면 즉시 reject + Opik anomaly log.

---

## 3. Silent Semantic Failures (가장 위험)

### #10 Wrong relationship direction
**증상**: 결과는 나오지만 의미적으로 반대. `(:Risk)-[:HAS_RISK]->(:Company)` 같은 거꾸로 패턴.
**검출**: ontology에 정의된 방향 vs query에서의 방향을 비교. AST 파싱 필요.
**처방**: prompt에 ontology의 *source → target* 표기를 항상 함께 제공.

### #11 Missing existence filter
**증상**: `MATCH (c)-[:HAS_RISK]->() RETURN c.name` 같이 *존재만 알면 되는데* 카운트가 부풀려진다.
**검출**:
```python
def missing_distinct(cypher: str) -> bool:
    return 'count(' in cypher.lower() and 'DISTINCT' not in cypher.upper()
```
**처방**: aggregation에 DISTINCT 또는 `EXISTS { ... }` subquery 권장.

### #12 Temporal-ignorant query
**증상**: `valid_until` 이 지난 fact를 결과에 포함. 의미 실패 (Ch 1 §10 참고).
**검출**: query에 RELATED_TO/MENTIONS edge가 있는데 `temporal_range` 필터가 없으면 경고.
**처방**: time-aware view를 별도 함수로 wrap — `where_valid_at(query, when)`.

---

## 4. Provider별 실패 빈도 매트릭스 (예시 / 실측 가이드)

> 실제 수치는 강의 실습에서 측정. 아래는 *경향성* 가이드 (5점 척도, 1=거의 안 함, 5=자주).

| Pattern | OpenAI | Kimi | DeepSeek | Grok |
|---|---|---|---|---|
| #1 Label hallucination | 2 | 3 | 2 | 4 |
| #2 Property typo | 2 | 2 | 3 | 3 |
| #3 Bidirectional miss | 3 | 4 | 2 | 3 |
| #4 LIMIT missing | 3 | 2 | 3 | 4 |
| #5 Injection vulnerability | 1 | 1 | 1 | 2 |
| #6 Unbounded path | 2 | 3 | 2 | 4 |
| #7 Cartesian product | 3 | 4 | 3 | 4 |
| #8 Null crash | 4 | 3 | 4 | 3 |
| #9 Read-only violation | 1 | 1 | 1 | 1 |
| #10 Wrong direction | 3 | 4 | 3 | 4 |
| #11 Missing DISTINCT | 4 | 4 | 5 | 4 |
| #12 Temporal-ignorant | 5 | 5 | 5 | 5 |

학습자에게: **#12 는 모든 provider가 잘 못 한다**. temporal-aware view를 *prompt 외부*에서 강제하는 게 가장 안전.

---

## 5. 방어 우선순위

12개를 모두 한 번에 막을 필요는 없다. ROI 순:

1. **#5 + #9 (injection / write op)** — 보안 단단히. read-only session으로 백엔드 강제.
2. **#4 + #6 (LIMIT / unbounded)** — 비용/안정성. post-validation으로 자동 추가/거부.
3. **#1 + #2 (label/property)** — prompt 보강. ontology slice 정확도가 좌우.
4. **#11 + #12 (DISTINCT / temporal)** — 결과 의미 정확도. wrapper function 패턴.
5. **#3 + #7 + #10 (방향/cartesian)** — AST parsing 도입 시.
6. **#8 (null)** — runtime monitoring.

---

## 6. 통합 validator 인터페이스

12개 검출기를 단일 함수로 묶어 generated Cypher를 즉시 채점:

```python
from dataclasses import dataclass

@dataclass
class CypherIssue:
    code: str
    severity: str  # 'block' | 'warn' | 'info'
    detail: str

def validate_cypher(cypher: str, *, ontology) -> list[CypherIssue]:
    issues = []
    if labels := detect_label_hallucination(cypher):
        issues.append(CypherIssue('#1-label', 'block', f'unknown labels: {labels}'))
    if has_write_op(cypher):
        issues.append(CypherIssue('#5-injection', 'block', 'destructive op'))
    if unbounded_path(cypher):
        issues.append(CypherIssue('#6-unbounded', 'block', 'path *.. without upper bound'))
    if missing_limit(cypher):
        issues.append(CypherIssue('#4-limit', 'warn', 'no LIMIT clause'))
    if missing_distinct(cypher):
        issues.append(CypherIssue('#11-distinct', 'warn', 'count() without DISTINCT'))
    # ... 추가 검출기
    return issues
```

**블로킹 정책**: `severity='block'` 1개라도 있으면 실행 거부, prompt에 issue를 다시 보내 self-revise (Ch 5 self-reflect 패턴 차용).

---

## 7. SEOCHO SDK 표준화 후보

현재 강의용 정규식은 모두 ad-hoc. SDK에 다음 surface를 노출하면 좋다:

```python
from seocho.query.guards import validate_cypher, CypherIssue

issues = validate_cypher(generated_cypher, ontology=onto)
if any(i.severity == 'block' for i in issues):
    raise CypherValidationError(issues)
```

→ bd 티켓: **"seocho.query.guards — 12-pattern Cypher validator + AST integration"** 후보.

---

## 8. 한 줄 요약

> *"executable rate 만 추적하면 silent semantic failure (#10~#12)를 놓친다. 결과의 *의미*까지 검증하는 단계가 별도로 필요하다."*
