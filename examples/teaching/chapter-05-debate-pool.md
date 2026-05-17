# Chapter 5. Debate Pool

## Learning Objectives
- 4가지 답변 생성 전략(single / self-reflect / multi-LLM debate / hybrid)을 정확도-비용 트레이드오프로 비교한다.
- Self-reflection의 2-pass 구조와, multi-LLM debate의 round / consensus / 종료 조건을 설계한다.
- Opik trace에서 reasoning step 수와 답변 품질의 상관을 측정한다.

## Prerequisites
- Chapter 1~4 완료 (특히 §4.4 인용 강제 답변)
- 다중 모델 액세스 (OpenAI / Anthropic / 로컬 중 최소 2종)
- Opik 워크스페이스

## 5.1 Single-LLM, Single Model (Baseline)

### Setup
- 1개 모델 × 1회 호출
- 입력: §4.3 aggregated context + §4.1 augmented query

### Baseline Metric
| 지표 | 측정 방법 |
|---|---|
| 정확도 | gold answer 대비 entailment / exact match |
| 환각률 | 인용되지 않은 사실 문장 비율 |
| 토큰비용 | input + output token 합 |
| Latency | end-to-end p50 / p95 |

### Hands-on
> TODO: 평가셋 20개 질문으로 baseline 측정 → 4가지 지표 표 작성.

### Code Anchor
- `seocho/agents.py`

---

## 5.2 Self-Reflection

### 2-Pass 구조
1. **Pass 1 (Draft)**: 평소대로 답변 생성
2. **Critic prompt**: "위 답변의 약점/근거 부족/오류 가능성을 비판하라"
3. **Pass 2 (Revise)**: 비판을 반영해 재생성

### Critic Prompt 설계
- 비판은 *구체적 문장 단위*로 요구 (모호한 "전반적으로 ..." 금지)
- 비판 결과가 비었으면 Pass 2 생략 (cost saving)

### Hands-on
> TODO: §5.1과 동일한 평가셋에서 self-reflection 적용 → 정확도 ↑ / 비용 ×2 트레이드오프 확인.

### Checkpoint
- Critic이 잘못된 비판을 하면 Pass 2 답변이 더 나빠질 수 있다. 어떻게 방어하는가? (Pass 1과 Pass 2 비교 후 더 잘 인용된 쪽 채택)

---

## 5.3 Multi-LLM Debate

### Setup
- 2~3개 모델(예: GPT-4o / Claude / 로컬)
- 각 모델이 독립적으로 초안 답변 생성
- Round 진행 — 다른 모델의 답변에 *반박/보완* 응답
- Moderator 모델이 최종 종합

### 핵심 설정
| 항목 | 권장값 | 비고 |
|---|---|---|
| Round 수 | 2 | 3 이상은 비용 대비 수익 체감 |
| Consensus threshold | 0.8 | 모델 간 답변 임베딩 유사도 |
| 종료 조건 | consensus 도달 또는 max round | early stop으로 비용 절감 |
| Moderator 역할 | 종합 + 인용 통합 + 거절 처리 | §4.4 envelope 강제 |

### Hands-on
> TODO: FinDER 질문 중 *논쟁 여지 있는* 5개 선정 → debate 결과의 다양성과 수렴 양상 분석.

### Checkpoint
- 모델 간 답변이 완전히 모순될 때 moderator는 어떻게 처리해야 하는가? (양쪽 인용 + uncertainty 명시, 또는 거절)

---

## 5.4 Self-Reflect + Debate (Hybrid)

### 아이디어
각 모델이 *자기 답변을 self-reflect 한 후* debate 라운드에 들어감.
→ debate 첫 라운드의 발산이 줄고, 수렴이 빨라짐.

### Flow
```
[Model A] draft → A's critic → A's revised
[Model B] draft → B's critic → B's revised
                                  ↓
                          Debate Round 1
                                  ↓
                       Moderator final answer
```

### 비용 분석
- 호출 수: `N_models × 2 (draft+revise) + N_models × R (debate round) + 1 (moderator)`
- vs §5.3: debate round 수를 1 줄여도 동등한 품질을 내는지 확인

### Hands-on
> TODO: §5.3과 §5.4를 같은 5개 질문에서 비교 → reasoning step 수 vs 정확도 산점도.

### Checkpoint
- self-reflect로 이미 답변이 강건해진 경우 debate가 *오히려* 답변을 흐리지 않는가? (drift) 어떻게 측정?

---

## 비교 요약 (예상 형태)

| 전략 | 정확도 | 환각률 | 토큰비용(상대) | Latency(상대) |
|---|---|---|---|---|
| §5.1 Single | baseline | baseline | 1.0× | 1.0× |
| §5.2 Self-Reflect | ↑ | ↓↓ | 2.0× | 2.0× |
| §5.3 Multi-LLM Debate | ↑↑ | ↓ | 3~5× | 3× |
| §5.4 Hybrid | ↑↑↑ | ↓↓ | 4~6× | 3~4× |

(실제 값은 평가셋 결과로 채워야 함)

---

## Opik Trace 분석

### 측정 항목
- Reasoning step 수 vs 정확도
- 모델별 token 비용 분포
- Debate round별 답변 유사도 변화 (수렴 곡선)

### Hands-on
> TODO: Opik에서 4개 전략 trace 비교 → reasoning depth와 정확도의 상관계수.

---

## Deliverables
- [ ] 평가셋 20개 (질문 + gold answer + 인용 가능 chunk)
- [ ] 4개 전략 결과 비교 표 (정확도/환각률/비용/latency)
- [ ] 5개 논쟁성 질문의 debate transcript
- [ ] Opik trace 캡처 (수렴 곡선)
