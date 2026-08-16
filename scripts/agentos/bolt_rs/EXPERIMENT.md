# Multi-agent × scalability × context-interchange (replay)

단일 에이전트 측정(소비 20.8 vs 생산 2.9 µs/row, 파이썬 스레드 천장 ~1.3코어)의 다음
질문: **N개의 에이전트가 각자 세션과 트랜잭션을 쥐고 동시에 뛰면 agent↔DB 교환의 어디가
부러지나.** LLM 호출 없는 replay — 에이전트의 DB측 행동(인덱스 룩업, 페이징, 쓰기 tx,
행→컨텍스트 직렬화)만 재생한다.

하네스: `neo4j` crate 0.2.0 (robsdedude, sync API) → DozerDB 5.26.3.
에이전트 = OS 스레드 1 + 세션 1 + op당 명시적 tx. `Arc<Driver>` 공유(커넥션 풀).
모든 op는 행 전량을 owned 값으로 drain 후 JSON/CSV 직렬화 비용을 별도 계측한다
(= LLM 컨텍스트로 넘기는 interchange 비용).

## 가설 → arm

| arm | 가설 | 확정 기준 |
|---|---|---|
| `scale` | 행→컨텍스트 변환은 N에 선형으로 쌓이지만 네이티브 thread-per-agent는 박스/서버 한계까지 스케일 (파이썬은 1.3코어에서 침몰) | agg rows/s가 N≥8까지 준선형, client_cpu_cores가 1.3을 훌쩍 넘김 |
| `mix` | in_context식 무거운 페이저 K명이 룩업 에이전트의 p99를 부풀린다 | K=0→6에서 lookup p99 단조 증가, p50 대비 배율 확대 |
| `contend` | 같은 허브 노드 쓰기 tx는 락에서 직렬화 — same-target p99가 N에 비례, spread는 평평 | same의 p99/mean이 N에 비례 성장, spread 대비 갭 |
| `dedup` | DB 결과→컨텍스트 경로엔 캐시 계층이 없다: 동일 결과셋도 에이전트마다 전액 지불, 절약은 서버(page cache)만 | same/distinct의 per-op 클라이언트 비용 동일, redundancy_factor = N×R, 첫 호출만 느림 |

## 실행

```
cargo build --release
python ../scripts/bench_multiagent.py           # 전체 스윕, 매니페스트 부착
./target/release/rust-harness scale finbenchl100 8 15   # 단건 (JSON → stdout)
```

- `scale <db> <N> <episodes>` — 에피소드 = 앵커 1-hop 룩업(LIMIT 200) + 정렬 없는
  스트림 페이지 5×200행. 정렬 페이징을 안 쓰는 이유: 서버 top-N 정렬(l100에서 ~1.4s/op)이
  클라이언트 소비 비용을 가려버린다. 그 정렬은 mix의 페이저 몫.
- `mix <db> <N> <K> <secs>` — 페이저 K명(ORDER BY amount DESC + SKIP 페이징),
  나머지는 룩업 루프. 데드라인 방식.
- `contend <N> <same|spread> <writes>` — **격리 DB `agentcontend`** (CAccount 1,000 +
  id 인덱스; 최초 실행 시 자동 생성). finbenchl\*에는 어떤 쓰기도 하지 않는다.
- `dedup <db> <N> <R> <same|distinct>` — 동일 앵커 vs 에이전트별 앵커로 같은 룩업 R회.

앵커는 상위 out-degree 계정 32개(런 시작 시 조회) — agent_interaction.py의 앵커 선정과
같은 정신. 워크스페이스 스코프 `_workspace_id:"default"`, 200행/call은 in_context arm의
row cap과 동일.

## 출력

arm당 JSON 1개: `{arm, db, params, wall_s, client_cpu_cores, agg_rows_per_s, ops:{op별
p50/p99/mean/…}, samples:[per-op raw]}`. 래퍼가 `results/bench/multiagent_<arm>_<ts>.json`
으로 runmeta 매니페스트와 함께 저장 (raw samples 규칙).

## 비스코프 (다음 라운드)

MARA/LLM 에피소드 루프 포팅 · vLLM prefix-cache A/B(셀프호스팅 필요) · 재전송 토큰 배율
로그 분석 · handle arm(행 대신 핸들, AIsummit26-zda)의 동시 부하 무대.
