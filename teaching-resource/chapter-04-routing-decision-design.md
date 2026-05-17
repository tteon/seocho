# Chapter 4 — Appendix: Routing Decision Design

> Ch 4 본편이 *4-axis augmentation + RRF 통합*의 메커니즘을 다뤘다면, 이 부속 문서는 **라우팅 결정의 의사결정 트리**, **신뢰도 임계값**, **컨텍스트 윈도 예산**, 그리고 **Ch 1 §10 temporal staleness penalty**를 라우팅 confidence에 어떻게 반영할지 정리한다.

## 0. 왜 결정 *설계*가 분리되어야 하는가

라우팅 코드는 한 번 짜면 끝이 아니라 *언제 어떤 백엔드를 신뢰하느냐*가 계속 바뀐다. 결정 기준을 코드 안에 묻으면 바꾸기 어렵고, *외부 설정*으로 빼면 너무 추상적이다. 적절한 layer는: **명시적 의사결정 트리 + 측정 가능한 임계값 + temporal-aware confidence**.

---

## 1. Routing Decision Tree (전체)

```
사용자 질의
   │
   ├─ Intent 분류 (Axis 1)
   │     ├─ lookup       → entity 식별됨?
   │     │                    ├─ yes → Cypher (단독)
   │     │                    └─ no  → Fulltext+Cypher (2단)
   │     ├─ aggregation  → Cypher (단독) + GDS 보조
   │     ├─ comparison   → 3-backend 모두 + rerank
   │     └─ explanation  → Vector + Fulltext (보조)
   │
   ├─ Topic 매핑 (Axis 3)
   │     └─ community_id 매칭 → 검색 범위를 community ±1 로 좁힘
   │                            (매칭 실패 시 전체 범위)
   │
   ├─ Confidence gate
   │     intent.confidence < 0.6 → all-backend safe fallback
   │     entity.confidence < 0.5 → entity-aware path 차단
   │     temporal_staleness > τ  → confidence × staleness_penalty
   │
   └─ Backend 가중치 → RRF 통합 → top-N → answer 생성
```

각 분기마다 다음을 측정·로깅한다 (Opik trace에 metadata):
- `decision.intent`, `decision.intent_confidence`
- `decision.entities_identified` (몇 개?)
- `decision.community_match` (success/fallback)
- `decision.gate_triggered` (어느 gate?)
- `decision.final_weights` (vector/fulltext/cypher)

---

## 2. Confidence Thresholds — 측정 가능한 값

휴리스틱이 아니라 *실측 기반*으로 결정한다. 강의에서는 다음 default를 쓰되, 데이터셋·도메인에 따라 fine-tune.

| Threshold | Default | Why this value |
|---|---|---|
| `INTENT_HIGH` | 0.80 | 4-provider 합의 시 도달 빈도 높음 |
| `INTENT_FALLBACK` | 0.60 | 이하면 safe all-backend |
| `ENTITY_KEEP` | 0.50 | 모호한 NER도 후속 검색에서 보강 가능 |
| `ENTITY_HARD_USE` | 0.75 | Cypher 직접 매칭에 사용 가능 |
| `COMMUNITY_NARROW` | 0.55 | community 매칭 그 자체의 prob 임계 |
| `STALENESS_SOFT_DAYS` | 30 | published_at 기준 30일 초과는 점진 페널티 |
| `STALENESS_HARD_DAYS` | 365 | 1년 초과는 routing에서 제외 (옵션) |

각 threshold마다 **fail-safe direction** 명시: 임계값을 못 넘으면 *어느 쪽으로 fallback* 하는지. 예: `INTENT_FALLBACK` 미달 시 fallback 방향 = "all backends with equal weight" (정확도↓ but coverage↑).

---

## 3. Context Window Budget

답변 생성 단계의 context는 토큰 비용·attention dilution을 동시에 좌우. 예산은 모델별로 다르고, RRF top-N 선정에 직접 영향.

```python
MODEL_CONTEXT = {
    'gpt-4o-mini'         : 128_000,
    'kimi-k2.5'           : 200_000,
    'deepseek-chat'       : 64_000,
    'grok-4.20-reasoning' : 128_000,
}

def context_budget(model: str, *, system_tokens: int, question_tokens: int,
                   answer_reserve: int = 1500) -> int:
    cap = MODEL_CONTEXT[model]
    available = cap - system_tokens - question_tokens - answer_reserve
    # 90% 만 채워서 attention dilution 방지
    return int(available * 0.90)
```

**RRF top-N 결정** (적응형):
```python
def adaptive_top_n(budget: int, *, avg_chunk_tokens: int = 220) -> int:
    return max(3, min(20, budget // avg_chunk_tokens))
```

이렇게 하면 같은 질의도 *어떤 모델로 답변하느냐*에 따라 컨텍스트 폭이 자동 조정됨.

---

## 4. Temporal Staleness Penalty (Ch 1 §10 연계)

문서의 신선도에 따라 confidence를 곱셈 페널티로 깎는다. 시간이 흐를수록 천천히 감소하는 exponential decay 사용:

```python
import math
from datetime import datetime

def staleness_penalty(published_at: datetime | None, *, half_life_days: float = 180) -> float:
    """0..1; 신선할수록 1.0. half_life_days 마다 절반."""
    if published_at is None:
        return 1.0  # 알 수 없으면 페널티 X (보수적)
    days = (datetime.utcnow() - published_at).days
    if days <= 0:
        return 1.0
    return math.exp(-math.log(2) * days / half_life_days)
```

라우팅에 적용:
```python
effective_confidence = raw_confidence * staleness_penalty(chunk.source.published_at)
```

**도메인별 half-life 가이드**:
- 회사 재무 / 가격 / 거시 지표: 30~90일
- 위험·규제·법적 변경: 90~180일
- 기업 구조 / 임원 / 자회사: 365일+
- 도메인 정의·용어: ∞ (페널티 없음)

Ch 1 property design 의 `temporal_range` 필드와 결합하면 *시점-인식 confidence*가 완성된다.

---

## 5. Backend별 강점 매트릭스 — 가중치의 *이유*

가중치 표 자체는 본편에 있다. 여기서는 *왜* 그 가중치인지의 근거를 부연.

| Backend | 강점 | 약점 | 사용을 멈춰야 할 때 |
|---|---|---|---|
| **Vector** | semantic similarity, paraphrase 대응 | entity 명 정확 매칭 약함 | 질의에 unique identifier (LEI, CIK) 명시될 때 |
| **Fulltext** (BM25) | 키워드 정확, 법령/조항 추적 | 어휘 미스매치에 약함 | 의미적 paraphrase 검색 |
| **Cypher** | 구조 관계 추적, count/aggregate 정확 | 자연어 paraphrase·oblique reference 약함 | entity 모호 + intent=explanation |

이 표를 *agent에게도 prompt로 보여주면* agent가 자기-라우팅 가능 (Ch 2 패턴 차용).

---

## 6. Rerank 정책 — RRF 이후의 한 단계

RRF는 *순위 통합*은 잘 하지만 *의미적 정확도*는 아니다. comparison/explanation intent 에서 top-N 이 3 이상이면 LLM rerank를 한 단계 더.

```python
def llm_rerank(question: str, candidates: list[dict], *, top_n: int) -> list[dict]:
    """LLM 에 question + 후보 list 를 보내 의미적 관련성 순위만 받기."""
    prompt = (
        f'Question: {question}\n\n'
        'Candidates:\n' +
        '\n'.join(f'[{i}] {c["text"][:200]}' for i, c in enumerate(candidates)) +
        '\n\nReturn ONLY a JSON list of indices ordered by relevance, top first.'
    )
    # ... LLM 호출 후 indices 적용
```

비용 일을 일으키므로 *intent confidence 가 낮을 때만* rerank.

---

## 7. 거절 (Refusal) 결정 트리

언제 답변 대신 거절을 반환할지의 트리:

```
fused_top_N 결과 평가
   │
   ├─ N = 0                                → 거절: "관련 컨텍스트 없음"
   ├─ avg(effective_confidence) < 0.3      → 거절: "신뢰도 부족"
   ├─ temporal_staleness == 0 (모두 stale) → 거절 + 신선한 데이터 요청 안내
   └─ otherwise                            → 답변 (인용 강제 + confidence tag)
```

거절도 Opik trace에 *거절 사유*를 metadata로 적재 → 거절 분포 분석으로 라우팅 약점 발견.

---

## 8. SEOCHO SDK 표준화 후보

현재 노트북은 라우팅 결정을 매번 inline 으로 코딩. SDK에 다음을 노출하면 좋다:

```python
from seocho.routing import RoutingPolicy, RoutingDecision

policy = RoutingPolicy.default()  # 또는 from_yaml('policy.yaml')
decision: RoutingDecision = policy.decide(question=q, augmentation=aug,
                                          model='gpt-4o-mini')
# decision.weights, decision.budget, decision.gate_triggered, decision.refusal
```

→ bd 티켓: **"seocho.routing — declarative routing policy with decision logging"** 후보.

---

## 9. 한 줄 요약

> *"라우팅 정확도는 모델이 아니라 *측정 가능한 임계값과 명시적 결정 트리*에서 온다. confidence를 곱셈으로 깎고, 거절 사유를 trace로 남겨라."*
