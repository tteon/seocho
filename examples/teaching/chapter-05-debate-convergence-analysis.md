# Chapter 5 — Appendix: Debate Convergence Analysis

> Ch 5 본편이 4가지 전략(single / self-reflect / debate / hybrid) 메커니즘을 다뤘다면, 이 부속 문서는 **수렴 곡선의 정의**, **early-stop 기준**, **participant 선정 휴리스틱**, **moderator 페일오버 정책** 을 정리한다.

## 0. "수렴"이라는 단어를 정확히 정의하기

debate 컨텍스트에서 *수렴*은 흔히 "답이 안 바뀜"으로 쓰이지만 정작 측정 가능한 지표로 옮길 때 모호하다. 다음 세 정의 중 하나를 선택해 일관되게 측정해야 한다.

| 정의 | 측정 방법 | 장점 | 단점 |
|---|---|---|---|
| **A. 답변 텍스트 유사도** | round-pair embedding cosine ≥ 0.95 | 가장 단순 | paraphrase 변형에 false positive |
| **B. 인용 set 일치** | 라운드별 인용된 (src, chunk) 집합의 Jaccard ≥ 0.8 | 의미 변화에 민감 | 인용 강제 prompt 필수 |
| **C. 사실 주장 일치** | LLM-as-judge 가 \"fact set\" 동등성 평가 | 가장 정확 | 가장 비싸고 모델 의존적 |

**권고**: 본 강의에선 **B (인용 set Jaccard)** 를 default. citation 강제는 Ch 4에서 이미 인프라가 있고, 측정 비용도 낮다.

---

## 1. 수렴 곡선 그리기 — 측정 코드 윤곽

```python
import numpy as np

def convergence_curve(per_round_panels: list[dict[str, str]]) -> list[float]:
    """라운드별 panel 답변들에 대해 *서로* 의 citation Jaccard 평균을 반환.
    per_round_panels: [{participant: answer, ...}, ...]
    return: 라운드 별 [0..1] (1.0 = 완전 일치)
    """
    curve = []
    for panel in per_round_panels:
        cite_sets = [extract_citations(a) for a in panel.values()]
        pair_jaccards = []
        for i in range(len(cite_sets)):
            for j in range(i + 1, len(cite_sets)):
                a, b = cite_sets[i], cite_sets[j]
                pair_jaccards.append(len(a & b) / max(1, len(a | b)))
        curve.append(float(np.mean(pair_jaccards)) if pair_jaccards else 0.0)
    return curve
```

각 라운드의 Jaccard 평균 ≥ 0.8 이면 *수렴 도달*. 곡선 시각화는 강의 슬라이드의 핵심 demo.

---

## 2. Early-Stop 기준

debate는 round 수가 늘수록 비용이 선형 증가, 정확도 개선은 체감. 다음 중 하나라도 충족하면 멈춘다:

| 기준 | 임계값 | 의미 |
|---|---|---|
| **Convergence reached** | round Jaccard ≥ 0.80 | 의견 일치 |
| **No improvement** | 직전 라운드 대비 Jaccard 변화 < 0.05 (2회 연속) | 정체 |
| **Hard cap** | round 수 ≥ 3 | 비용 상한 |
| **Time budget** | 누적 latency ≥ 60s | 사용자 인내 한계 |
| **Cost budget** | 누적 토큰 ≥ 30k | 강의용 cost guard |

```python
def should_stop(curve: list[float], elapsed_ms: int, tokens: int, max_rounds: int = 3) -> tuple[bool, str]:
    if not curve:
        return False, ""
    last = curve[-1]
    if last >= 0.80:
        return True, f"convergence reached ({last:.2f})"
    if len(curve) >= 2 and abs(curve[-1] - curve[-2]) < 0.05:
        if len(curve) >= 3 and abs(curve[-2] - curve[-3]) < 0.05:
            return True, "no improvement (2 stagnant rounds)"
    if len(curve) >= max_rounds:
        return True, f"hard round cap = {max_rounds}"
    if elapsed_ms >= 60_000:
        return True, "time budget exceeded"
    if tokens >= 30_000:
        return True, "token budget exceeded"
    return False, ""
```

각 stop 조건도 Opik trace에 *어느 조건이 트리거됐는지* 기록.

---

## 3. Participant 선정 휴리스틱

4-provider를 항상 다 쓰면 비싸다. 질의 타입에 따라 participants를 선별.

| 질의 타입 | 권장 participants | 이유 |
|---|---|---|
| **factual lookup** (수치/날짜) | OpenAI + DeepSeek | 결정적, JSON-mode 안정 |
| **explanation / synthesis** | OpenAI + Kimi | 긴 context, 표현 다양성 |
| **comparison / debate-worthy** | OpenAI + Kimi + DeepSeek + Grok | 4 모두 (시각 다양성) |
| **edge case (논쟁성)** | OpenAI + Grok (heavy reasoning) | reasoning depth |

**위임 패턴**: intent classifier (Ch 4 §1) 결과를 받아 자동으로 participants 결정.

```python
def select_participants(intent: str, available: list[str]) -> list[str]:
    presets = {
        'lookup'      : ['openai', 'deepseek'],
        'aggregation' : ['openai', 'deepseek'],
        'explanation' : ['openai', 'kimi'],
        'comparison'  : ['openai', 'kimi', 'deepseek', 'grok'],
    }
    desired = presets.get(intent, ['openai'])
    return [p for p in desired if p in available] or available[:1]
```

---

## 4. Moderator Failover

moderator는 최종 합성을 하므로 *단일 실패점*. 다음 정책으로 안정성 확보.

### 4.1 Primary / Fallback 페어
```python
MODERATOR_CHAIN = ['openai', 'kimi', 'deepseek']  # 순서대로 시도

def moderate(panel: dict[str, str], context: str) -> dict:
    for cand in MODERATOR_CHAIN:
        if not available_providers().get(cand):
            continue
        try:
            return _do_moderate(cand, panel, context)
        except Exception as exc:
            log_anomaly('moderator_failed', provider=cand, error=str(exc))
            continue
    raise RuntimeError("all moderators failed — escalate to human")
```

### 4.2 Moderator self-check
moderator가 답변 합성 후 *자기 답변에 invalid citation이 있는지* self-check. invalid > 0 이면 chain의 다음 moderator로 fallback (Ch 4 §4 인용 검증 차용).

### 4.3 Conflict resolution policy
participants의 답이 모순일 때 moderator의 default 동작:
- (보수) 모순을 explicit 으로 인용 둘 다 + uncertainty 명시
- (적극) majority 답 + 소수 의견 footnote
- (회피) 거절 — \"의견이 분열되어 단일 답변 어렵습니다\"

기본은 *보수*. 도메인이 명확한 정답을 요구할 때만 *적극*.

---

## 5. 비용·품질 Pareto Frontier

전략별 비용·정확도 곡선을 측정해 사용자가 *비용 한도* 내에서 가장 좋은 전략을 고를 수 있게:

```python
def pareto_frontier(records: list[dict]) -> list[dict]:
    """records: [{'strategy': str, 'cost': float, 'accuracy': float}, ...]"""
    sorted_r = sorted(records, key=lambda r: r['cost'])
    frontier = []
    best_acc = -1
    for r in sorted_r:
        if r['accuracy'] > best_acc:
            frontier.append(r)
            best_acc = r['accuracy']
    return frontier
```

강의 demo: 4 strategy × 8 FinDER 카테고리 × N 샘플 → Pareto frontier 산점도 → \"Hybrid이 항상 최고는 아니다\" 가 보임.

---

## 6. Debate Anti-Patterns

자주 빠지는 함정:

| 함정 | 증상 | 처방 |
|---|---|---|
| **Echo chamber** | 모든 participant 가 첫 라운드부터 동일 답 | 강제 diversity prompt (\"반드시 다른 시각 제시\") |
| **Sycophancy cascade** | round마다 답이 점점 비슷해짐 (수렴이 아닌 동조) | round별로 critic agent 별도 — 합성과 분리 |
| **Moderator bias** | moderator 가 자기 prior 답을 우선 | moderator 모델을 매 round 회전 |
| **Hidden context drop** | round 2+에서 원 context를 까먹음 | 매 round prompt에 context 재첨부 |
| **Citation drift** | round마다 인용 set 이 *늘어*나기만 함 | round 종료마다 evidence 중복 제거 단계 |

이 5가지는 *수렴 곡선의 모양*만 봐도 식별 가능 (단조 증가 vs 진동 vs 정체) — 강의에서 직접 곡선을 그려보게 한다.

---

## 7. SEOCHO SDK 표준화 후보

현재 강의 노트북은 debate orchestration을 inline 으로 작성. 이미 `extraction/debate.py:DebateOrchestrator` 가 존재하지만 SDK 표면 (`seocho.debate.run`) 으로 노출되어 있지 않음. 다음을 노출하면 좋다:

```python
from seocho.debate import DebatePolicy, run_debate

policy = DebatePolicy(
    participants=['openai', 'kimi'],
    moderator_chain=['openai', 'kimi'],
    max_rounds=3,
    convergence_threshold=0.80,
)
result = run_debate(question, context, policy=policy)
# result.curve, result.stop_reason, result.final_answer, result.cost
```

→ bd 티켓: **"seocho.debate — SDK surface for DebatePolicy with convergence telemetry"** 후보.

---

## 8. 한 줄 요약

> *"debate 의 비용은 round 수에 비례하고, 정확도 개선은 체감한다. 수렴을 *정의*하고, *측정*하고, *멈춰라*."*
