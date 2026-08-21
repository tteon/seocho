# Agent ↔ database interaction, by audience, difficulty, scale and agent design

Model `gpt-oss-120b` · 468 episodes · 3 repeats per cell · row cap 50 · transaction timeout 60s · plan-gate probe 2.0s.

Latency figures come from replaying the query each design settled on 100 times with no model in the loop, first execution discarded. That is a deliberate split: a p99 needs about a hundred samples per cell, and the number an operator is on the hook for is the tail of the query a design ships, not the variance of the model that wrote it.

## The questions

| id | audience | difficulty | asked as |
|---|---|---|---|
| `ext_easy_1` | external | easy | 내 계좌로 지금까지 들어온 이체는 몇 건이고 총액은 얼마인가요? |
| `ext_easy_2` | external | easy | 내 계좌에서 나간 이체 건수와 그 중 가장 큰 금액은? |
| `ext_med_1` | external | medium | 내 계좌로 돈을 보낸 계좌 중 고위험 채널을 쓴 곳은 어디인가요? |
| `ext_med_2` | external | medium | 내가 송금한 계좌들의 실제 소유자는 누구인가요? |
| `ext_hard_1` | external | hard | 내 돈이 두 단계 안에 닿는 계좌는 몇 개이고, 그 중 가장 위험한 등급은? |
| `ext_hard_2` | external | hard | 내 계좌로 두 단계 안에 돈이 흘러들어온 계좌는 몇 개인가요? |
| `int_easy_1` | internal | easy | 전체 계좌 수와 최고위험(등급 5) 계좌 수는? |
| `int_easy_2` | internal | easy | 거래가 가장 많이 오간 채널 상위 5개는? |
| `int_med_1` | internal | medium | 같은 사람이 소유한 계좌끼리 직접 송금이 오간 사례는 몇 건인가요? |
| `int_med_2` | internal | medium | 100곳이 넘는 상대로부터 입금을 받은 계좌는 어디인가요? |
| `int_hard_1` | internal | hard | 서로 돈이 오가고, 소유자끼리 보증을 서줬고, 같은 기기로 로그인한 계좌 쌍을 찾아주세요. |
| `int_hard_1b` | internal | hard | 서로 돈이 오가고, 소유자 중 한 쪽이 다른 쪽에 보증을 섰고, 같은 기기로 로그인한 계좌 쌍은? |
| `int_hard_2` | internal | hard | 여러 차명계좌에서 한 계좌로 신고기준 아래 금액만 잘게 모으고 있는 사람은 누구인가요? |

## 1. Cost and correctness per agent design

| scale | agent design | correct | db hits (median) | round trips | chars into context | guardrail rejects | plan rejects | timeouts |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| SF1 | labels only | 36/39 | 59,082 | 1.0 | 111 | 0 | 0 | 0 |
| SF1 | + ontology | 36/39 | 59,082 | 1.0 | 100 | 0 | 0 | 0 |
| SF1 | + guardrail | 36/39 | 59,082 | 1.0 | 100 | 4 | 0 | 0 |
| SF1 | + plan feedback | 36/39 | 16,705 | 1.0 | 158 | 6 | 0 | 0 |
| SF10 | labels only | 30/39 | 542,593 | 1.0 | 95 | 0 | 0 | 0 |
| SF10 | + ontology | 36/39 | 542,593 | 1.0 | 109 | 0 | 0 | 0 |
| SF10 | + guardrail | 36/39 | 542,593 | 1.0 | 109 | 3 | 0 | 0 |
| SF10 | + plan feedback | 34/39 | 104,825 | 1.0 | 169 | 6 | 0 | 0 |
| SF100 | labels only | 36/39 | 5,381,224 | 1.0 | 128 | 0 | 0 | 0 |
| SF100 | + ontology | 36/39 | 5,381,224 | 1.0 | 103 | 0 | 0 | 0 |
| SF100 | + guardrail | 36/39 | 5,381,224 | 1.0 | 103 | 3 | 0 | 0 |
| SF100 | + plan feedback | 36/39 | 1,789,272 | 1.0 | 180 | 6 | 15 | 1 |

## 2. p99 latency by question

| question | agent design | SF1 p99 (ms) | SF10 p99 (ms) | SF100 p99 (ms) |
|---|---|---:|---:|---:|
| ext_easy_1 | labels only | 1 | 4 | 18 |
| ext_easy_1 | + ontology | 1 | 4 | 19 |
| ext_easy_1 | + guardrail | 2 | 4 | 19 |
| ext_easy_1 | + plan feedback | 2 | 5 | 17 |
| ext_easy_2 | labels only | 2 | 3 | 12 |
| ext_easy_2 | + ontology | 2 | 4 | 19 |
| ext_easy_2 | + guardrail | 1 | 9 | 18 |
| ext_easy_2 | + plan feedback | 1 | 5 | 21 |
| ext_med_1 | labels only | 6 ✗ | 21 ✗ | 116 (33%) |
| ext_med_1 | + ontology | 2 | 4 | 19 |
| ext_med_1 | + guardrail | 2 | 5 | 21 |
| ext_med_1 | + plan feedback | 1 | 5 | 20 |
| ext_med_2 | labels only | 3 | 14 | 71 |
| ext_med_2 | + ontology | 3 | 12 | 72 |
| ext_med_2 | + guardrail | 3 | 13 | 74 |
| ext_med_2 | + plan feedback | 2 | 19 | 67 |
| ext_hard_1 | labels only | 62 | 575 | 7,623 |
| ext_hard_1 | + ontology | 58 | 600 | 5,942 |
| ext_hard_1 | + guardrail | 58 | 552 | 5,861 |
| ext_hard_1 | + plan feedback | 4 | 18 | 368 |
| ext_hard_2 | labels only | 4 | 21 | 193 |
| ext_hard_2 | + ontology | 4 | 18 | 164 |
| ext_hard_2 | + guardrail | 3 | 16 | 166 |
| ext_hard_2 | + plan feedback | 3 | 19 | 148 |
| int_easy_1 | labels only | 2 | 12 | 91 |
| int_easy_1 | + ontology | 3 | 13 | 109 |
| int_easy_1 | + guardrail | 4 | 13 | 114 |
| int_easy_1 | + plan feedback | 3 | 12 | 95 |
| int_easy_2 | labels only | 42 | 458 | 4,721 |
| int_easy_2 | + ontology | 12 | 148 | 1,436 |
| int_easy_2 | + guardrail | 12 | 140 | 1,470 |
| int_easy_2 | + plan feedback | 12 | 95 | 951 |
| int_med_1 | labels only | 29 | 306 | 3,692 |
| int_med_1 | + ontology | 18 | 117 | 1,155 |
| int_med_1 | + guardrail | 13 | 116 | 1,179 |
| int_med_1 | + plan feedback | 21 | 160 | 1,704 |
| int_med_2 | labels only | 10 | 107 | 1,639 |
| int_med_2 | + ontology | 11 | 106 | 1,597 |
| int_med_2 | + guardrail | 9 | 108 | 1,352 |
| int_med_2 | + plan feedback | 13 | 92 | 1,128 |
| int_hard_1 | labels only | 26 | 10,895 ✗ | 3,385 |
| int_hard_1 | + ontology | 16 ✗ | 203 ✗ | 2,483 ✗ |
| int_hard_1 | + guardrail | 14 ✗ | 187 ✗ | 2,444 ✗ |
| int_hard_1 | + plan feedback | 15 ✗ | 198 ✗ | 2,125 ✗ |
| int_hard_1b | labels only | 28 | 254 ✗ | 3,840 |
| int_hard_1b | + ontology | 20 | 225 | 3,013 |
| int_hard_1b | + guardrail | 20 | 249 | 3,271 |
| int_hard_1b | + plan feedback | 25 | 216 (33%) | 2,726 |
| int_hard_2 | labels only | 31 | 210 | 5,696 (67%) |
| int_hard_2 | + ontology | 36 | 331 | 4,318 |
| int_hard_2 | + guardrail | 41 | 352 | 4,344 |
| int_hard_2 | + plan feedback | 31 | 155 | 3,344 |

✗ marks a cell whose answer never matched gold; a percentage marks partial agreement across repeats. A fast wrong answer is not a result.

## 3. What the guardrail rejected

| agent design | rejection reason | count |
|---|---|---:|
| + guardrail | `unknown_relationships` | 10 |
| + plan feedback | `unknown_relationships` | 15 |
| + plan feedback | `missing_parameterized_limit` | 3 |
| + plan feedback | `missing_parameter` | 3 |

## 4. Cells where the design did not reproduce

| scale | question | agent design | distinct queries | correct |
|---|---|---|---:|---:|
| SF1 | ext_med_1 | + guardrail | 3 | 100% |
| SF10 | int_hard_2 | + plan feedback | 3 | 100% |
| SF1 | ext_easy_2 | + ontology | 2 | 100% |
| SF1 | ext_hard_1 | + guardrail | 2 | 100% |
| SF1 | int_hard_1 | labels only | 2 | 100% |
| SF1 | int_hard_1b | labels only | 2 | 100% |
| SF1 | int_hard_2 | labels only | 2 | 100% |
| SF1 | int_hard_2 | + ontology | 2 | 100% |
| SF1 | int_hard_2 | + plan feedback | 2 | 100% |
| SF10 | ext_easy_1 | + guardrail | 2 | 100% |
| SF10 | ext_easy_1 | + ontology | 2 | 100% |
| SF10 | ext_easy_2 | + guardrail | 2 | 100% |
| SF10 | ext_easy_2 | + plan feedback | 2 | 100% |
| SF10 | int_hard_1 | labels only | 2 | 0% |
| SF10 | int_hard_1 | + plan feedback | 2 | 0% |
| SF10 | int_hard_1b | + plan feedback | 2 | 33% |
| SF10 | int_hard_2 | labels only | 2 | 100% |
| SF10 | int_med_1 | labels only | 2 | 100% |
| SF100 | ext_easy_1 | + guardrail | 2 | 100% |
| SF100 | ext_easy_2 | + guardrail | 2 | 100% |
| SF100 | ext_easy_2 | labels only | 2 | 100% |
| SF100 | ext_easy_2 | + plan feedback | 2 | 100% |
| SF100 | ext_hard_1 | labels only | 2 | 100% |
| SF100 | ext_hard_2 | labels only | 2 | 100% |
| SF100 | ext_med_1 | labels only | 2 | 33% |
| SF100 | ext_med_2 | labels only | 2 | 100% |
| SF100 | int_hard_1 | + plan feedback | 2 | 0% |
| SF100 | int_hard_2 | labels only | 2 | 67% |
