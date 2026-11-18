# 설계안: LLM I/O contract와 MARA conformance harness (`seocho-llm-io`)

- 상태: 제안 (draft, review r1 반영. 변경 대응표는 §12)
- 대상: Rust workspace (`rust/`, 신규 crate 3개, `seocho-core`에 binding module 1개), Python package (`src/seocho`, shadow 호출 지점 1곳)
- 관점: 두 초안을 합친 결과입니다. **contract-first** 초안(A)에서 contract 정의와 identity를, **operations-first** 초안(B)에서 기록 형식, 검사 loop, 운영 장치를 가져왔습니다. 축별 선택과 이유는 §3.2에 있습니다.
- 경로 약어:
  - `rust:` = Rust workspace (`rust/`). 예: `rust:crates/seochod/src/main.rs` = `rust/crates/seochod/src/main.rs`.
  - `room:` = 이 저장소의 Python 소스. `room:src/seocho/store/llm.py`와 `src/seocho/`를 생략한 `room:store/llm.py`는 같은 파일입니다. 접두어 없는 `llm.py:N` 등은 바로 앞에 인용한 파일입니다.
  - `harness:` = survey 대상이었던 내부 실험 harness입니다. 이 저장소에 포함되지 않으므로 파일 경로와 행 번호 대신 이름만 남깁니다: `harness:shared-knowledge` (multi-agent MARA recording harness. `RRTransport`와 `sk-llm/1` 형식, `BudgetGuard`의 출처), `harness:graphcot` (GraphCoT read-path agent harness), `harness:finder` (FinDER multi-instance extraction harness. finder-v1 receipt의 출처).
  - `data:` = 비공개 실험 데이터 root (git 밖, 공개하지 않음).
- `sr-*` ID는 Rust workspace 쪽 issue tracker의 ticket입니다: sr-z5g = MARA 402/tool_choice 오류 분류, sr-jsw = `seocho-core`의 Python parity 검증, sr-5ev·sr-mz6 = native가 없을 때 조용히 통과하는 fallback/parity test. `seocho-*` ID는 이 저장소의 issue입니다.
- 이 설계의 `seocho.llm_exchange.v1` record 형식 (§7.1)은 `rust/crates/seocho-llm-conformance/spec/`에 normative spec, JSON Schema, golden fixture로, stdlib Python reference validator와 fixture generator는 같은 crate의 `python/`에 있습니다.
- 이 문서의 현재 코드 관련 주장은 2026-10-25 읽기 전용 survey와 위 경로의 file:line에 근거합니다 (행 번호는 작성 시점 기준이며 이후 변경으로 어긋날 수 있습니다). "추정"으로 표시한 수치는 해당 milestone gate에서 측정합니다.

---

## 1. 배경과 문제

### 1.1 출력 형식은 모델마다 다르고, 그 흡수는 seocho의 책임입니다

MARA (`https://api.cloud.mara.com/v1`, SambaNova 기반, room:src/seocho/store/llm.py:1309)의 2026-10-25 live catalog에는 5개 모델이 있습니다: DeepSeek-V3.1, DeepSeek-V3.2, MiniMax-M3, gemma-4-31B-it, gpt-oss-120b. 이 모델들은 같은 OpenAI 호환 endpoint에서 서로 다른 wire 형식을 냅니다. 아래 표는 **MARA에서 기록으로 관측한 것**과 **예상만 하는 것** (redactor regex, 다른 provider, 코드 주석이 근거)을 나눕니다. profile의 `supports`/`think_markup` 사실은 앞 표의 관측만 근거로 쓸 수 있습니다 (§4.6 evidence lint).

**(a) MARA에서 관측 (recording 또는 receipt 근거)**

| 변형 | 관측 위치 |
|---|---|
| 결과가 `message.content`가 아니라 `message.reasoning`에 있고 content가 빔 | MiniMax-M2.7, ERB extraction chunk의 약 26% (seocho-ub5) |
| gpt-oss는 forced tool call에서도 `message.reasoning`을 항상 같이 반환 | finder-v1 http-state (gpt-oss-120b extraction run. 값은 `clean()`이 `[redacted]`로 치환) |
| forced tool call: `content=null`, `function.arguments`가 **문자열로 직렬화된** JSON | finder-v1 receipts (gpt-oss-120b, MiniMax-M3 전부) |
| `finish_reason=length` 잘림 | MiniMax-M3 GraphCoT 2298 turn 중 15 |
| 잘림을 body가 아니라 HTTP 400으로 반환 ("Could not finish the message because max_tokens or model output limit was reached") | gpt-oss-120b, finder-v1 sample 약 340개 중 22 |
| HTTP 400 "did not output valid json" / "truncated before a complete json" | MiniMax-M2.7 under `json_object` (room:store/llm.py:663-664) |
| HTTP 400 "Specified tool_choice ... zero valid function call" (`error_model_output`에 partial reasoning, harmony 구간 포함) / "...function call to another tool" | gpt-oss-120b (sr-z5g), MiniMax-M3 (약 318 중 6) |
| HTTP 400 본문에 "did not complete" (tool_choice auto, gpt-oss 불완전 tool call, T=0에서 결정적) | harness:graphcot recovery 조건 (T=0.3 → 0.6 → fallback model) |
| HTTP 402 `PAYMENT_METHOD_REQUIRED` (`error.code`, `balance_units`, `billing_portal_url`) | harness:shared-knowledge live smoke의 preflight recording (실제 402 body), sr-z5g |
| HTTP 429, 502 (concurrency 상황) | seocho-c2a5, seocho-zpoj |
| usage 확장: `completion_tokens_details.reasoning_tokens`, `time_to_first_token`, `total_latency`, `completion_tokens_per_sec` | raw body에는 있지만 seocho `LLMResponse.usage`는 reasoning_tokens를 버림 (room:store/llm.py:1115-1152) |

**(b) 예상 (MARA 기록 근거 없음)**

| 변형 | 근거 |
|---|---|
| reasoning carrier `reasoning_content` | Kimi K2.5 (room:store/llm.py:1133-1136 주석). MARA 모델에서는 `reasoning`만 관측 |
| thinking markup: `<think>`, `<mm:think>`, 닫히지 않은 `<think>`, content 안의 harmony `<\|channel\|>analysis<\|message\|>` | room:src/seocho/llm_structured.py:129 `_THINK_RE` (`<think>`만 처리)와 실험 redactor regex (harness:finder telemetry). 표본 MARA receipt (harness:shared-knowledge의 live smoke/synthetic recording)에서 literal think tag는 0건. harmony는 gpt-oss 400의 `error_model_output` 안에서만 관측 |
| tool_choice auto에서 native tool_calls와 함께 `content`에 prose thought | MiniMax-M3 survey 메모. GraphCoT JSONL의 `thought`는 reasoning field에서 오므로 (harness:graphcot) 근거가 되지 않습니다. M1 canary에서 recording을 확보해 fixture로 고정합니다 (§4.4 E 규칙) |
| 앞뒤 prose, ```` ```json ```` fence, `{nodes, relationships}` 대신 bare array | room:src/seocho/index/extraction_engine.py:116-185 `normalize_payload`가 처리하는 형태. 어느 모델에서 얼마나 나오는지는 M1 baseline에서 측정 |

### 1.2 출력 계약이 문장으로만 존재합니다

ontology는 LLM에 **prompt text로만** 전달됩니다. 렌더링 경로는 `Ontology.to_extraction_context` (room:src/seocho/ontology/core.py:1729), `to_linking_context` (:1820), `to_query_context` (:1747), `to_semantic_prompt_context` (:1250)입니다. 출력 shape은 Jinja template 안의 손으로 쓴 문장 하나입니다: "Return JSON with "nodes" and "relationships" keys" (room:src/seocho/query/strategy.py:75-351).

- ontology를 JSON Schema로 변환하는 코드는 Python에도 Rust에도 없습니다. seocho core가 `structured_complete`에 넘기는 JSON Schema는 손으로 쓴 `_SCHEMA` 하나뿐입니다 (room:src/seocho/eval/text2cypher_judge.py:20). 그나마 capability가 `supports_guided_json`일 때만 `json_schema`로 전송됩니다 (room:llm_structured.py:239). server 단위로 schema를 강제한다고 보는 provider는 `{"vllm"}`뿐이고 (:83), openai는 model 이름 regex (:63-64)로 같은 capability를 얻습니다.
- 적합성은 사후에만 검사합니다: `Ontology.validate_extraction(closed=)` (core.py:1977), `to_shacl`/`validate_with_shacl` (core.py:1123, :1286), `score_extraction` (core.py:2075), `EnforcementPolicy` strict|guided|open (room:src/seocho/index/enforcement.py:18).
- 결과가 어느 계약 version에 맞춰 검증됐는지 receipt에 남지 않습니다. `ontology_context_hash`는 ontology identity만 나타냅니다 (room:src/seocho/ontology/context.py:301). 출력 shape, 검증 rule, salvage policy는 hash 바깥에 있습니다.

### 1.3 오늘의 흡수 코드는 흩어져 있고 일부는 위험합니다

| # | 위험 | 위치 |
|---|---|---|
| H1 | 재시도가 두 층으로 겹칩니다. (1) seocho ladder: `_completion_retry_variants` (room:store/llm.py:696)가 만든 최대 4개 variant를 bare `Exception`으로 잡으며 돌립니다 (:806/:863, :948/:1005). 그래서 401/402도 variant마다 재전송되고 ladder 자체에는 backoff가 없습니다. (2) 그 아래 OpenAI SDK: client kwargs에 `max_retries`가 없어 (:194-198) SDK 기본값 (2회, 408/409/429/5xx에 backoff)이 각 variant 안에서 돕니다. 그래서 429는 SDK가 backoff로 재시도하고, 논리 호출 1개가 HTTP 요청 약 12개로 불어날 수 있습니다. harness:shared-knowledge는 이것을 피하려고 자체 transport를 씁니다 | room:store/llm.py:194-198, :696-720, :806-870 |
| H2 | content가 비면 reasoning prose를 답 text로 반환합니다. 그 뒤 salvage가 prose 속 예시 JSON을 캘 수 있습니다. seocho-ub5는 이것이 strict-enforcement H1 측정을 오염시킨다고 기록합니다. `_build_response`는 `message.tool_calls`를 읽지 않으므로 forced tool 응답 (content=null, gpt-oss는 reasoning 동반)에서는 reasoning prose가 답 text가 됩니다 | room:store/llm.py:1115-1152, room:llm_structured.py:132 |
| H3 | `<mm:think>`, harmony, 닫히지 않은 think를 처리하지 않습니다. salvage는 dict만 반환하므로 array는 직접 `json.loads`가 성공할 때만 살아남습니다 | room:llm_structured.py:129-144, room:store/llm.py:210 |
| H4 | parser가 넷이고 실패 처리도 제각각입니다: `LLMResponse.json` + `extract_json_object` (최대 object 선택), greedy regex `\{.*\}` (room:query/semantic_decompose.py:55, room:index/extraction_critique.py:117, room:query/grounded_text2cypher.py:66,125), think 제거 없는 `_safe_json` (room:src/seocho/tools.py:24), Agents SDK 경로의 `json.loads` (room:integrations/openai_agents.py:207,220,280). 실패 시 동작도 빈 결과, 기본 intent, heuristic fallback, 예외 전파가 섞여 있습니다 | survey의 parse site 16곳 이상 |
| H5 | raw provider body가 seocho에 남지 않습니다. tracing은 content capture가 켜졌을 때만 completion을 2000자로 잘라 남깁니다. seocho-fix.11이 요구하는 per-call `ok\|salvaged\|failed`와 reasoning/output token 분리도 span에 없습니다 | room:src/seocho/tracing.py:77 |
| H6 | `MaraBackend` 직접 생성 시 기본 timeout은 120 s입니다 (preset은 900 s). 기본 모델 MiniMax-M2.7은 live catalog에 없습니다. `ROLE_FALLBACKS`에는 M2.5가 남아 있습니다 | room:store/llm.py:1309, :~107-120; harness:shared-knowledge config |
| H7 | extraction은 vLLM에서도 항상 `json_object`를 씁니다. ADR-0098 guided_json 변환은 제거되었습니다 | room:index/extraction_engine.py:94; room:store/llm.py:~603 |

### 1.4 검사 loop가 없습니다

- **record/replay 구현이 실험마다 따로 있습니다.**
  - `RRTransport` (harness:shared-knowledge)의 `sk-llm/1`이 현존 형식 중 가장 좋습니다. reasoning을 포함한 응답 내용, `req_hash`/`occurrence` 기반 replay, classification을 함께 보존합니다. 다만 **raw bytes는 아닙니다**: 응답을 `json.loads(raw)`한 dict로 저장하고, JSON이 아닌 body는 앞 4,000자만 `unparsed_body`로 남깁니다. replay도 `json.dumps(body)`로 다시 만든 bytes를 돌려줍니다. harness에 commit된 fixture는 prompt를 `messages_sha256`로 줄입니다 (`_slim_request`).
  - finder-v1 receipts (data: 아래 http-state, 4050건)도 parsed `response` dict이고, `clean()`이 key 이름이 `reasoning(_content)?|error_model_output`과 맞는 값을 모두 `[redacted]`로 바꾸고 문자열 안의 think/harmony 구간을 지웁니다 (harness:finder telemetry). 이 key 규칙은 `message.reasoning`뿐 아니라 `usage.completion_tokens_details.reasoning_tokens`와 tool_choice 400의 `error_model_output`에도 걸립니다. 그래서 gpt-oss body는 전부 변형되었고, finder-v1에서는 reasoning-fallback 경로, reasoning token 수, `error_model_output`을 복원할 수 없습니다.
  - GraphCoT run JSONL (harness:graphcot)은 timing과 token만 있고 raw body가 없습니다.
- **catalog drift를 감지하는 자동 장치가 없습니다** (H6이 그 결과입니다).

### 1.5 Rust 쪽 현재 상태

- rust:crates의 어느 crate도 LLM I/O를 하지 않습니다. HTTP client, JSON Schema crate, prompt 처리가 모두 없습니다.
- offline 의존성 확인 결과 (rust:Cargo.lock, `~/.cargo/registry/cache`):
  - Cargo.lock에 있음: `serde 1.0.229`, `serde_json 1.0.154`, `regex 1.13.1`, `memchr 2.8.3`, `sha2 0.11.1`, `hex 0.4.3`, `pyo3 0.23.5`.
  - registry cache에만 있음: `reqwest 0.12.28`, `tokio 1.53.x`.
  - 없음: `jsonschema`, `schemars`, `flate2`, `zstd`, `insta`, `criterion`, `proptest`.
- 재사용할 수 있는 조각:
  1. `seocho-core::rules::infer_rules_from_nodes` (rust:crates/seocho-core/src/rules.rs:123). `required|datatype|enum|range` rule을 Python과 byte-for-byte 같게 생성합니다. insertion-ordered `PyValue`와 `json.dumps` 호환 writer `write_py_json`을 갖고 있습니다. 단 이 writer는 `json.dumps` **기본** separator (`", "`, `": "`)를 씁니다 (rules.rs:7-8). contract hashing에 필요한 `_stable_json`은 `sort_keys=True, separators=(",",":")`입니다. 그래서 writer를 옮길 때 separator와 key 정렬을 parameter로 만들어야 합니다. 또 `seocho-core`는 `crate-type = ["cdylib"]`만 있으므로 다른 crate가 이 writer에 의존하려면 rlib으로 옮겨야 합니다. 그리고 `rules.rs`는 module 맨 위에서 `use pyo3::prelude::*`를 합니다 (rules.rs:9). 그래서 이것은 단순 이동이 아니라 **module 분할**입니다: `PyValue`/`write_py_json`/rule 생성 logic은 pyo3 없는 쪽으로, `#[pyfunction]` wrapper만 `seocho-core`에 남깁니다.
  2. `seochod`의 `Node`/`Relationship` (rust:crates/seochod/src/main.rs:87-103)과 `SemanticReceipt` (:65-71). extraction 계약의 downstream 소비자입니다. 단 seochod는 bin 전용 crate (`src/{main,batch,bolt}.rs`)이고 이 type들은 private입니다. 같은 Rust type을 공유하려면 type을 별도 rlib으로 추출해야 하고 seochod의 의존성이 바뀝니다. 이 작업은 첫 deliverable에 넣지 않습니다 (§9 M5+).
  3. `oxigraph_read_model`: bundle sha256에 묶인 term lookup sidecar입니다. class/property schema export는 없습니다.

---

## 2. 목표 / 비목표

### 목표

- **G1. Pure core.** `seocho-llm-io`는 raw response bytes와 compiled contract를 받아 typed, ontology-validated 결과와 diagnostics와 다음 행동 계획을 냅니다. network, clock, env, filesystem, random 접근이 없습니다.
- **G2. Contract-first.** ontology와 task spec에서 `OutputContract`를 **결정적으로** compile합니다. 모든 parse site, constrained-decoding 요청, prompt의 OUTPUT_SCHEMA section이 contract 하나에서 나옵니다.
- **G3 (후속, M5+). Contract identity를 receipt chain에 넣습니다.** `contract_sha256`이 기존 agent profile을 거쳐 `projection_receipt_sha256`에 전이적으로 들어갑니다. seochod wire는 바꾸지 않습니다 (§5.5). parse/repair loop가 최소 한 task에서 authoritative가 되기 전에는 하지 않습니다. 첫 deliverable에서 contract hash는 recording과 interpretation receipt의 key로만 씁니다.
- **G4. 모든 모델 출력 수용.** 수용은 **data인 model profile**과 공통 pipeline이 맡습니다. Rust code는 새로운 *종류*의 wire 변형(예: 새 markup 문법)에만 씁니다. constrained decoding (strict `json_schema`, vLLM structured outputs, forced tool)을 지원되는 곳에서 우선합니다.
- **G5. 빠른 offline 검사.** committed corpus 전체 replay가 `cargo test`에서 **2 s 미만**에 끝납니다 (debug, 단일 thread). private corpus 전체 (약 1만 recording)는 release build `llmconf replay`로 **30 s 미만**입니다.
- **G6. 싼 live 검사.** MARA live canary를 하루 4회, 주간 sweep을 1회 돌리고 **월 $60 이하 (추정)**로 유지합니다. 월 $100 hard cap을 넘으면 기계적으로 멈춥니다 (§7.4).
- **G7. 측정과 자동 등록.** model × task × mode별로 outcome 비율, diagnostic code 분포, prompt/completion/reasoning token, latency, TTFT, 비용을 수치로 냅니다. 회귀는 bd issue로 등록합니다 (중복 없음, run당 상한, 처음 2주는 dry-run).
- **G8. 1시간 onboarding.** 새 모델이나 형식 변화를 1시간 안에 onboarding합니다. 대부분은 profile data 변경으로 끝납니다.
- **G9. Strangler-fig migration.** Python을 golden oracle로 두고, shadow, task별 authoritative, 은퇴 순서로 진행합니다. sr-jsw parity 방식처럼 차이를 분류합니다. oracle은 task carrier별로 둘입니다: content carrier task는 `_build_response` → `LLMResponse.json` → `extract_json_object`, tool_call task는 실험 parser (§7.7). 결정 table은 §7.7에 있습니다.

**첫 deliverable (M1, lean).** T/E/P decode (content와 tool_call carrier), raw-byte recording, T1 committed replay, MARA canary. 아래는 명시적으로 **뒤로 미룹니다**: G3/§5.5 receipt chain 연결, `Exposure::Vocabulary`, M3b Rust live recorder, bd issue 자동 close, Python 쪽 contract compiler/hash parity, seochod type 추출. 이 항목들은 parse/repair loop가 최소 한 task에서 authoritative가 된 뒤 (M5 이후) 다시 판단합니다.

### 비목표

- **Rust HTTP client의 즉시 작성.** 녹화는 M3까지 Python recorder (RRTransport 확장)가 맡습니다. Rust live recorder는 선택 milestone이고 Q4 결정에 따릅니다.
- **extraction firewall 정책 변경.** strict-mode prompt line을 ontology와 무관하게 고정하는 규칙 (room:index/extraction_engine.py:30-50, FinDER r=-0.76)은 그대로 둡니다. decode-time schema에 ontology vocabulary를 넣는 것은 기본 off인 별도 policy입니다 (§5.3).
- **prompt 작성과 template 이전.** `prompt_ir.py`의 Prompt Package IR은 Python에 남습니다. 이 crate는 OUTPUT_SCHEMA section의 **내용**과 요청 body 생성만 맡습니다.
- **모델 선택 정책** (ModelRouter, room:src/seocho/routing/model_router.py:85). 이 crate는 측정치와 `FallbackModel` 신호만 냅니다. 어느 모델로 갈지는 caller가 정합니다.
- **streaming 응답의 정식 지원.** recorder는 non-streaming만 기록합니다. Gemma streaming 이슈 (seocho-zpoj)는 별도로 다룹니다.
- **free-text answer 품질 평가** (room:query/answering.py:139). reasoning leak 검사만 합니다.
- **완전한 JSON Schema draft 구현.** generator가 내는 subset만 검증합니다.
- **seochod wire 변경.** node에 contract hash를 stamp하는 것은 선택 사항입니다 (Q2).

---

## 3. 검토한 대안

### 3.1 구조 대안

| | 요약 | 판정 |
|---|---|---|
| **A** parser-first | `extract_json_object`를 Rust로 port하고 markup 지원을 넓힘 | 기각. H4의 근본 원인 (계약이 문장으로만 존재)이 남습니다. "무엇이 성공인가"가 parse site마다 다르고 receipt에도 남지 않습니다 |
| **B** Python harness 확장 | `RRTransport`와 `sk-llm/1`을 Python package (`src/seocho`) 공용 module로 승격하고 parser는 Python에 둠 | 기각. Rust rewrite의 interface owner가 생기지 않습니다. 다만 **recorder는 M1-M3 동안 이것을 재사용**합니다 |
| **C** provider-first | provider client, retry, HTTP를 Rust core에 함께 둠 | 기각. core가 tokio/reqwest/rustls에 의존하면 `cargo test` replay가 무거워지고 PyO3 binding에도 network stack이 따라옵니다. offline lock에 HTTP stack도 없습니다 |
| **D** contract + pure core + 별도 conformance bin | `seocho-contract` (ontology → contract), `seocho-llm-io` (bytes in, Outcome out), `seocho-llm-conformance` (`llmconf`: record 연동, replay, report, onboard, issues) | **채택** |

D를 고른 근거:
1. 같은 wire schema 하나가 OpenAI `response_format.json_schema`, vLLM structured outputs, MARA forced tool `parameters`로 **lowering**됩니다. constrained decoding (G4)과 사후 검증 (G2)이 같은 정의를 씁니다.
2. pure core이므로 녹화한 bytes가 그대로 test fixture가 됩니다. 검사 loop가 싸고 빠른 이유가 여기에 있습니다.
3. contract hash는 receipt chain의 기존 확장 지점 (agent profile)에 들어가므로 seochod를 바꾸지 않아도 됩니다.

### 3.2 두 초안의 충돌과 선택

| 축 | A (contract-first) | B (ops-first) | 선택과 이유 |
|---|---|---|---|
| contract 정의 | 3층 (js1 wire schema, lowering, semantic rule), `Exposure`, 3종 hash, receipt chain 연결 | `schema::from_ontology(Shape)`, `schema_sha256` | **A.** B의 `Shape::Projection` (seochod payload와 같은 type에서 생성)만 추가합니다. A는 OpenAI strict의 open-object 금지를 lowering으로 해결하고, firewall을 decode-time 불변식 (C3)으로 test합니다. B에는 이 둘이 없습니다 |
| 판정 API | `interpret<T: Domain>` → `Interpretation<T>` + `Validated<T>`, chain 수준 outcome (`Repaired` 포함) | `Interpreter` (embedded table, overlay), 절대 실패하지 않는 `Outcome`, per-attempt `Verdict`, `fingerprint` | **B를 base로, A의 type 안전성을 위에 얹습니다.** replay runner는 분기 없이 fingerprint만 비교해야 하므로 untyped `Outcome`이 기본입니다. typed 접근은 `Outcome::typed::<T>() -> Result<Validated<T>, _>`로 제공합니다. per-attempt verdict (`Repairable`, `Retryable`)는 B를, chain 수준 outcome (`Ok\|Salvaged\|Repaired\|Failed`, seocho-fix.11)은 metrics에서 A의 정의를 씁니다 |
| 다음 행동 | `next_action` (Resample, BoostBudget, Downgrade 등 명시 variant), `interpret`와 분리 | `next_step` (Retry{patch, backoff_ms}, Repair, FallbackModel, Stop) | **합칩니다.** 분리 (A)와 backoff, chain replay 검증 (B)을 모두 가져옵니다. retry 종류는 `RequestPatch` enum으로 명시합니다. A의 `Downgrade`(strip ladder 대체, 근거 code 필수)를 추가합니다 |
| diagnostic code | 계층 dotted code (`provider.payment_required`) + `Disposition` | flat code (`http402_payment_required`) + format detector | **A의 형식, B의 detector.** dotted code는 layer별 집계와 dedupe key에 유리합니다. B의 `profile_unmatched`, `unknown_wire_shape`는 형식 변화 감지기로 필수라서 `ops.profile_unmatched`, `transport.unknown_wire_shape`로 넣습니다 |
| 기록 형식 | `seocho.llm_exchange.v1`: `contract_sha256`, `wire_dialect`, `profile_sha256`, inline `expect` | `seocho-llm-rec/1`: raw bytes 보존 (`body_utf8`/`body_b64`), full request, `chain`, `live_outcome`, `redaction` | **B의 내용, A의 이름 체계와 identity field.** 이름은 기존 `seocho.lifecycle.v1`, `seocho.output_contract.v1`과 맞춰 `seocho.llm_exchange.v1`로 합니다. expectation은 B처럼 recording 밖 (`expect/<major>/`)에 둡니다. recording은 불변이어야 하고 expectation은 bless로 바뀌기 때문입니다 |
| redaction | committed fixture의 reasoning을 합성 text로 치환 | 출력은 건드리지 않고 입력 분류 (`input_class`)로 committed 여부 결정 | **B.** body를 고치면 `body_sha256`과 fidelity가 깨집니다. A의 합성 치환은 별도 `origin=synthetic-derived` recording (자기 sha를 가짐)으로만 허용하고, live 관측으로 집계하지 않습니다 |
| 검사 loop | nightly 약 150 call matrix, 7회 중앙값 + 2σ | T0-T6 계층, BudgetGuard, catalog probe, Wilson 하한, dedupe, dry-run, 자동 close, onboarding runbook | **B.** 사용자 요구 ("자주, 싸고 빠르게")에 직접 답합니다. A의 "판정이 바뀐 live row를 `corpus/regressions/`로 복사해 fixture로 승격"하는 경로를 추가합니다 |
| 성능 gate | `#[ignore]` benchmark가 예산 초과 시 실패, interpret p99 < 200 µs | test 안에서 시간 assert 금지 (flaky), nightly rec/s 회귀 issue | **B의 방식, A의 수치.** 기본 `cargo test`는 시간을 assert하지 않습니다. A의 p99 예산은 release `#[ignore]` benchmark로 M2 종료 시 측정합니다 |
| Python binding | 별도 cdylib `seocho-llm-py`, 조용한 fallback 금지 | 기존 `seocho-core` PyO3에 module 추가 | **B의 위치, A의 fallback 정책.** wheel과 maturin build를 하나로 유지합니다. 단 `seocho_core/__init__.py:7-22`의 fallback 패턴을 따르지 않고 `seocho_core.llm_io`는 `_native` 부재 시 shadow에서는 loud skip + metric, rust mode에서는 `ImportError`입니다 (sr-5ev, sr-mz6 유형 방지) |
| crate 분할 | contract, llm-io, py, conformance (4개) | llm-io, conformance (2개) | **3개 + binding module.** `seocho-contract`를 분리하는 이유: canon writer를 rlib으로 옮겨야 하고 (§1.5), seochod의 `Projection` contract test와 `seocho-core`의 rule inference가 parser 없이 이것만 의존할 수 있습니다 |
| 첫 milestone | `seocho-contract` + hash parity | 기록 형식 + importer + Python baseline | **B를 기본으로, 범위를 더 줄입니다 (r1).** M1은 raw 기록, T/E/P decode, T1 replay, canary입니다. `seocho-contract`와 L/V/S는 M2로 옮깁니다. T/E/P는 contract 없이도 carrier, finish, error 분류를 판정할 수 있고, 사용자 요구 (모든 모델 형식 수용 + 싸고 잦은 검사)에 가장 짧은 경로이기 때문입니다. 첫 PR은 RRTransport raw 저장과 기록 형식입니다 (§11) |
| MARA 기본 structured mode | ForcedTool, 실험 gate 뒤에만 | onboarding probe로 `json_schema_strict` 지원 여부를 확정할 때까지 tool → json_object | **둘을 결합합니다.** probe가 지원 여부를 *사실로* 확정하고 (B), 기본값 전환은 사전 등록 실험 gate를 통과한 뒤에만 합니다 (A) |

---

## 4. 아키텍처

### 4.1 구성도

```
          ┌──────────── scheduled (systemd user timer / paseo schedule) ──────────────┐
          v                               v                                          v
 llmconf catalog (hourly, 0 token)   llmconf canary (4x/day)              llmconf sweep (weekly)
          │ GET /v1/models                │ POST /v1/chat/completions (BudgetGuard)   │
          v                               v                                          v
 ┌──────────────────────────────────────────────────────────────────────────────────────────┐
 │ recorder: Python RRTransport 확장 (max_retries=0, raw bytes) / M3b Rust `live`는 후속    │
 │  writes seocho.llm_exchange.v1: full request + raw response bytes + timing + live_outcome │
 └───────────────────────────────────────────────┬──────────────────────────────────────────┘
                                                 v
 ┌──────────── corpus (content-addressed, append-only) ─────────────────────────────────────┐
 │ private:   data:llm-corpus/                      (full bytes, 0700, git 밖)              │
 │ committed: rust:crates/seocho-llm-conformance/corpus/  (input_class public|synthetic만)  │
 └───────────────────────────────┬──────────────────────────────────────────────────────────┘
                                 │ replay (network 없음, ReplayMiss = fail closed)
                                 v
 ┌──────────── seocho-llm-io (pure rlib) ───────────────────────────────────────────────────┐
 │ T decode → E extract → P parse/repair → L lower → V schema → S semantic → J verdict      │
 │ next_step(): Accept/Retry/Reprompt/Downgrade/FallbackModel/Stop  (계획만, 전송 안 함)    │
 │ plan_request() / build_request(): contract × profile × policy → provider JSON body       │
 │ profiles/*.json (embedded)                                                               │
 ├──────────── seocho-contract (pure rlib) ─────────────────────────────────────────────────┤
 │ OntologySnapshot + TaskSpec → OutputContract (js1 schema, lowering, rules, canon, hash) │
 └──────────────┬───────────────────────────────────────────────────┬───────────────────────┘
                v                                                   v
  metrics (seocho.llm_metrics.v1) → regress → bd issues     seocho-core `_native` + seocho_core.llm_io
                │                                                   → room shadow (complete_with_task_hints,
                v                                                     room:store/llm.py:363)
  Python oracle diff (nightly, carrier별 §7.7):
    content: store/llm.py:1115 → :210 → llm_structured.py:132
    tool_call: harness:finder extract_message, harness:shared-knowledge agents
```

### 4.2 crate와 process

| crate | 종류 | 의존성 (offline, Cargo.lock 존재) | 책임 | 금지 |
|---|---|---|---|---|
| `crates/seocho-contract` | rlib | serde, serde_json, sha2 0.11.1, hex | `OntologySnapshot`, `TaskSpec`, `OutputContract::compile`, js1 subset과 `CompiledSchema`, lowering, semantic rule, `canon` (Python 호환 JSON writer), hash | I/O, regex, pyo3 |
| `crates/seocho-llm-io` | rlib | seocho-contract, serde_json, regex, memchr | transport decode, payload extraction, 제한적 JSON repair, validate, verdict, `next_step`, `plan_request`/`build_request`, model profile, diagnostics, fingerprint | network, clock, fs, env, random |
| `crates/seocho-llm-conformance` | bin `llmconf` + rlib | 위 두 crate, serde_json. feature `live` (M3b, 후속·선택)에서만 reqwest 0.12.28 blocking + rustls | replay, report, diff-oracle, onboard, profile-suggest, bless, issues, (M3a) Python recorder subprocess 구동 | 기본 build에서 network |
| `crates/seocho-core` (기존) | cdylib (pyo3 0.23.5) | + 위 두 rlib | `_native.llm_io_*` 함수 노출, Python `seocho_core/llm_io.py` (fallback 없음) | 판정 logic |

- workspace lint를 그대로 따릅니다: `unsafe_code = forbid`, clippy deny `unwrap_used`/`expect_used`/`todo`/`dbg_macro`/`large_types_passed_by_value` (rust:Cargo.toml:19-27).
- **CI 불변식 (C1):** `cargo tree -p seocho-llm-io`와 `cargo tree -p seocho-contract`에 `reqwest|hyper|tokio|rustls|pyo3`가 나오면 실패합니다. core 소스의 `std::net|std::fs|std::env|SystemTime|Instant` 사용도 grep으로 금지합니다.
- `seocho-core` 정리: `rules.rs`를 pyo3 없는 부분과 `#[pyfunction]` wrapper로 **분할**하고 (rules.rs:9가 module 전체에서 pyo3를 import), `PyValue`와 `write_py_json`은 `seocho-contract::canon`으로 옮기고, `seocho-core::rules`가 이를 재사용합니다. `infer_rules_from_nodes`의 Python API와 출력 bytes는 바꾸지 않습니다 (기존 parity test가 그대로 통과해야 합니다).
- 압축: Rust에는 `flate2`가 없으므로 corpus는 **비압축 JSONL**입니다. 기존 `.jsonl.gz`는 Python importer가 풀어서 넣습니다 (Q5).

### 4.3 Public Rust API

```rust
// ---------- seocho-contract ----------
pub struct OntologySnapshot {              // M1: Python exporter, 이후 oxigraph_read_model `schema` op 후보 (Q3)
    pub ontology_context_hash: String,     // 16 hex, 그대로 보존 (context.py:46-47)
    pub rdf_bundle_sha256: Option<Digest64>,
    pub node_labels: Vec<Label>,
    pub relationship_types: Vec<RelSpec>,  // name, source/target label set, broader chain
    pub property_rules: Vec<PropertyRule>, // rules.rs kind 4종
    pub declares_generic_entity: bool,
}
pub struct TaskSpec { pub task: TaskKind, pub contract_rev: u32, pub exposure: Exposure, pub bounds: Bounds }
pub enum Exposure { Structural, Vocabulary }           // §5.3
pub enum Dialect { OpenAiStrict, Vllm, ToolParameters, JsonObjectPrompt }

pub struct OutputContract { /* private */ }
impl OutputContract {
    pub fn compile(task: &TaskSpec, onto: Option<&OntologySnapshot>) -> Result<Self, ContractError>;
    pub fn sha256(&self) -> &Digest64;
    pub fn wire_schema(&self, d: Dialect) -> &CanonicalJson;
    pub fn render_output_section(&self) -> String;     // prompt OUTPUT_SCHEMA. Structural이면 상수
}

/// V와 S를 통과한 값. validator만 생성합니다 (pub ctor 없음, Deserialize 없음).
pub struct Validated<T> { value: T, contract: Digest64 }

// ---------- seocho-llm-io ----------
pub struct RawExchange<'a> {
    pub request: &'a serde_json::Value,                // 실제로 보낸 body
    pub http_status: Option<u16>,                      // None => transport error
    pub body: &'a [u8],                                // provider bytes 그대로 (재직렬화 금지)
    pub transport_error: Option<TransportErrorClass>,  // timeout | connect | reset
}

pub struct CallSpec<'a> {
    pub provider: ProviderId,                          // mara | vllm | openai | deepseek | kimi | xai | qwen
    pub model: &'a str,
    pub contract: &'a OutputContract,
    pub policy: Policy,                                // §4.5
}

pub struct Interpreter { /* ProfileTable, immutable */ }
impl Interpreter {
    pub fn embedded() -> Result<Self, LoadError>;
    pub fn with_overlay(&self, profile_json: &str) -> Result<Self, LoadError>;   // onboarding (§7.6)
    pub fn interpret(&self, ex: &RawExchange<'_>, call: &CallSpec<'_>) -> Outcome;
    pub fn next_step(&self, out: &Outcome, call: &CallSpec<'_>, budget: &RetryBudget) -> NextStep;
    pub fn plan_request(&self, call: &CallSpec<'_>, attempt: &AttemptState) -> RequestPlan;
    pub fn build_request(&self, req: &RequestIr, plan: &RequestPlan) -> Result<serde_json::Value, BuildError>;
}

pub enum Verdict { Ok, Salvaged, Repairable, Retryable, Fatal }   // attempt 단위

pub struct Outcome {
    pub verdict: Verdict,
    pub value: Option<serde_json::Value>,      // lowered + validated payload
    pub degraded: Option<serde_json::Value>,   // guided: rule 위반을 경고로 강등한 값
    pub text: Option<String>,                  // free_text task
    pub tool_calls: Vec<ToolCall>,             // arguments는 문자열에서 decode 완료
    pub diags: Vec<Diag>,                      // 순서 고정, 안정 code (§6)
    pub usage: Usage,                          // prompt/completion/reasoning/cached + provider timing
    pub finish: Finish,                        // stop | length | tool_calls | content_filter | none | other
    pub receipt: InterpretationReceipt,        // seocho.llm_interpretation.v1 (§5.5)
    pub fingerprint: [u8; 32],                 // sha256(verdict, diag codes, canonical value/text)
}
impl Outcome {
    pub fn typed<T: Domain>(&self) -> Result<Validated<T>, Diag>;
}

pub enum NextStep {
    Accept,
    Retry { patch: RequestPatch, backoff_ms: u32, because: Code },
    Reprompt { append: Vec<Message>, because: Code },          // 이전 응답 + 위반 path 인용
    Downgrade { to: StructuredMode, because: Code },           // strip ladder 대체
    FallbackModel { because: Code },                           // 모델 선택은 caller
    Stop { because: Code },                                    // 401/402/403, budget, fatal
}
pub enum RequestPatch { MaxTokens(u32), Temperature(f32), Both { max_tokens: u32, temperature: f32 } }
pub enum StructuredMode { JsonSchemaStrict, GuidedJson, ForcedTool { name: String }, JsonObject, PromptOnly }

pub trait Domain: Sized { const TASK: TaskKind; fn from_lowered(v: &serde_json::Value) -> Result<Self, Diag>; }

/// 새 *종류*의 markup에만 구현합니다. 목록은 고정이고 늘릴 때 review 대상입니다.
pub trait ReasoningMarkup: Send + Sync {
    fn id(&self) -> &'static str;
    fn split(&self, text: &str) -> Option<MarkupSplit>;     // reasoning span, final span
}
```

설계 결정:
- **`interpret`는 절대 실패하지 않습니다** (`Result`가 아님). 깨진 bytes도 `Verdict::Fatal`과 diag를 가진 `Outcome`입니다. replay runner는 분기 없이 fingerprint만 비교합니다.
- **판정과 정책을 분리합니다.** `interpret`는 무엇이 왔는지만 판정하고 `next_step`은 무엇을 할지 계획만 합니다. 실제 전송은 caller (seocho runtime, `llmconf`)가 합니다. 그래서 retry 정책도 기록된 chain으로 replay 검증할 수 있습니다 (§7.2).
- **`backoff_ms`는 결정적 기본값입니다.** jitter는 random이 필요하므로 caller가 더합니다.
- **`build_request`가 pure한 이유:** recorder와 production이 같은 body를 만들어야 recording이 production을 대표합니다. 입력 `RequestIr`는 room:src/seocho/prompt_ir.py의 Prompt Package IR (PromptStage, OUTPUT_SCHEMA section, cache scope)을 따릅니다. `req_hash` 일치가 M3b의 종료 기준입니다.
- **비용:** 모든 regex, schema, profile은 `Interpreter::embedded`와 `OutputContract::compile`에서 한 번만 compile합니다. `interpret`는 body를 한 번 parse하고 (`serde_json::from_slice`) carrier 문자열을 한 번 scan합니다.

### 4.4 pipeline 단계

```
bytes ─T─> Decoded ─E─> candidate text ─P─> Value ─L─> lowered ─V─> js1 OK ─S─> rules ─J─> Outcome
```

| 단계 | 하는 일 | profile로 제어되는 것 | 주요 code |
|---|---|---|---|
| **T** transport decode | HTTP status 분류 (2xx / 400 subtype / 401 / 402 / 403 / 408 / 429 / 5xx). error body 분류: `error.message` 문구 match, `error_model_output`, 402 `error.code`/`billing_portal_url`. `choices[0].message`에서 `content`, `tool_calls`, `reasoning`, `reasoning_content` 수집 (attribute와 extra key 모두). `finish_reason`. usage 정규화 (`prompt_tokens_details.cached_tokens`, `usage.cached_tokens`, `completion_tokens_details.reasoning_tokens`, SambaNova timing). 해석 못 한 message 구조는 `transport.unknown_wire_shape` | `reasoning_fields` 순서, `error_patterns`, usage extra key | `provider.*`, `transport.*` |
| **E** extract | carrier 우선순위 결정: (1) native `tool_calls`가 있으면 `tool_choice`가 forced든 auto든 **tool args가 carrier**이고, 같이 온 content는 `extract.preamble_content` (Info)로만 남기며 `Outcome.text`나 답 후보가 되지 않습니다. (2) 그 외에는 content. (3) profile이 선언한 경우만 reasoning field. reasoning markup 분리 (`<think>`, `<mm:think>`, 닫히지 않은 think, harmony analysis → final). fence 제거. prose 앞뒤 제거. top-level `{...}`/`[...]` balanced span 후보 열거 (quote/escape aware, `extract_json_object` 알고리즘 일반화 + array 지원). native tool_calls 없이 textual tool markup만 있으면 diag (room:eval/agentic_graph_agents.py:187 규칙) | `think_markup`, `result_may_live_in_reasoning`, `salvage` | `extract.*` |
| **P** parse/repair | 후보 parse. 실패하면 **제한된** repair: trailing comma, 잘린 꼬리의 열린 괄호 닫기. 단일 따옴표 key 같은 추측성 repair는 하지 않습니다. 후보 선택은 "가장 큰 것"이 아니라 **L1 wire schema를 통과하는 후보 중 최대**이고, 통과 후보가 없으면 최대 후보입니다. `finish=length`이거나 꼬리를 닫았으면 `Salvaged`가 아니라 `Repairable`입니다 | `salvage` | `parse.*` |
| **L** lower | contract의 lowering 단계 실행 (§5.2 L2) | contract | `lower.*` |
| **V** schema | js1 검증. 위반은 JSON pointer path와 함께 모두 수집 (상한 64) | contract | `schema.*` |
| **S** semantic | ontology rule (§5.2 L3). severity는 `Policy.enforcement`가 결정 | contract, policy | `ontology.*` |
| **J** verdict | verdict, receipt, fingerprint 계산 | policy | — |

### 4.5 Policy와 reasoning carrier

`Policy = { enforcement: Strict | Guided | Open, salvage: SalvagePolicy, reasoning: ReasoningPolicy }`.

- `ReasoningPolicy::FromReasoningFieldIfDeclared`가 기본값입니다. reasoning field는 profile이 `result_may_live_in_reasoning: true`로 선언했을 때만 carrier가 됩니다 (MiniMax-M2.7). 그 경우에도 결과는 최대 `Salvaged`이고 `extract.reasoning_carrier` diag가 붙습니다.
- strict에서는 reasoning만 있고 content가 비면 판정이 `Repairable`이고 `transport.content_empty_reasoning_only`가 붙습니다. 오늘처럼 reasoning prose를 답으로 쓰지 않습니다. seocho-ub5의 H1 오염을 제거합니다.
- strict에서 `parse.repaired`와 `extract.reasoning_carrier`를 Terminal로 승격하는 option을 둡니다. salvage가 지나치게 관대해지는 것을 막습니다 (§10 리스크 7).
- rule 위반 severity는 contract가 아니라 `EnforcementPolicy` 의미를 따릅니다: strict이면 reject, guided이면 warn과 `degraded`, open이면 `_out_of_ontology` stamp (room:index/enforcement.py:116). 덕분에 같은 contract hash로 세 mode를 비교할 수 있습니다.

### 4.6 thin per-model adapter = data profile

adapter는 Rust code가 아니라 `profiles/<provider>/<model-glob>.json` data입니다. crate에 `include_str!`로 embed하고 `profile_sha256`으로 identity를 붙입니다.

```json
{
  "schema_version": "seocho.model_profile.v1",
  "profile": "mara/gpt-oss-120b", "profile_version": 3, "status": "active",
  "inherits": "mara/_provider",
  "match": {"provider": "mara", "model": "gpt-oss-120b"},
  "reasoning_fields": ["reasoning"],
  "think_markup": ["harmony"],
  "result_may_live_in_reasoning": false,
  "tool_args_encoding": "json_string",
  "request": {
    "max_tokens_floor": 1024,
    "temperature": {"omit": false, "omit_when_reasoning": false, "clamp": null},
    "extra": {"reasoning_effort": "low"},
    "structured_mode_preference": ["forced_tool", "json_object"],
    "supports": {"json_object": true, "json_schema_strict": "unknown", "tools": true, "forced_tool_choice": true}
  },
  "error_patterns": [],
  "evidence": ["rec:sha256:…", "bd:sr-z5g", "finder-v1 122x400/4050"]
}
```

provider 공통 문구는 model profile이 아니라 `profiles/mara/_provider.json`에 두고 모든 MARA model profile이 상속합니다. model profile의 `error_patterns`는 그 모델에만 있는 문구를 **앞에 덧붙일** 뿐이고 provider 목록을 지우지 못합니다.

```json
{
  "schema_version": "seocho.model_profile.v1", "profile": "mara/_provider", "abstract": true,
  "error_patterns": [
    {"status": 400, "contains": "zero valid function call", "code": "provider.tool_choice_unsatisfied"},
    {"status": 400, "contains": "function call to another tool", "code": "provider.tool_choice_wrong_tool"},
    {"status": 400, "contains": "did not complete", "code": "provider.tool_choice_unsatisfied"},
    {"status": 400, "contains": "max_tokens or model output limit", "code": "provider.output_limit_400"},
    {"status": 400, "contains": "did not output valid json", "code": "provider.json_truncation_400"},
    {"status": 400, "contains": "truncated before a complete json", "code": "provider.json_truncation_400"},
    {"status": 402, "error_code": "PAYMENT_METHOD_REQUIRED", "code": "provider.payment_required"}
  ],
  "evidence": ["rec:sha256:… (live smoke preflight 402)", "harness:graphcot recovery", "room:store/llm.py:663-664"]
}
```

- "did not complete"는 graphcot recovery가 실제로 trigger하는 문구입니다 (tool_choice auto, gpt-oss 불완전 tool call). "zero valid function call"과 같은 code로 묶습니다.
- 상속 순서: `default` → `<provider>/_provider` → `<provider>/<model>`. 같은 status에서 여러 pattern이 맞으면 목록 앞쪽이 이깁니다.

- **constrained decoding 우선.** `plan_request`는 `JsonSchemaStrict > GuidedJson > ForcedTool > JsonObject > PromptOnly` 순서로, profile `structured_mode_preference`에 있고 `supports`가 `true`이고 contract에 해당 dialect lowering이 있는 첫 mode를 고릅니다.
  - vLLM/openai는 `["json_schema_strict", "json_object"]`입니다 (vLLM 0.27 structured outputs, room:store/llm.py:1329).
  - MARA는 server-side `json_schema` 강제 증거가 없습니다. onboarding probe (§7.6)가 `supports.json_schema_strict`를 `unknown`에서 확정합니다. 그 전까지는 현재 동작대로 `json_object`를 쓰고, `ForcedTool{name: "submit_<task>"}` (gpt-oss/M3 function valid rate 1.0)로의 기본값 전환은 M6 실험 gate 뒤에만 합니다.
- **`evidence`**: profile의 각 사실이 corpus의 어느 recording에 근거하는지 가리킵니다. evidence 없는 `supports: true`는 profile lint 실패입니다. evidence recording은 wire 형태를 그대로 담은 것 (`body_fidelity: raw` 또는 harness가 재구성하지 않은 `reserialized`)이어야 합니다. harness가 다시 쓴 blob (예: mara bench `output_blob`의 object 형태 arguments)은 `source.wire: false`로 표시하고 profile-suggest와 evidence에 쓰지 않습니다.
- **`tool_args_encoding`**: 관측된 사실이며 `evidence`가 필요합니다. 파서는 어느 값이든 **두 형태를 모두 받습니다**. 문자열이면 `ParseStringifiedJson`을 거치고, 이미 object이면 그대로 쓰고 `lower.args_object` (Info)를 냅니다. object 인자를 `extract.tool_args_invalid_json`으로 판정하지 않습니다. 선언과 다른 형태가 n ≥ 1 관측되면 profile drift 신호입니다 (§7.5 새 code 규칙).
- **`status: retired`**: catalog에서 사라진 모델 (MiniMax-M2.7, M2.5)입니다. replay는 계속 하고, live 대상에서는 빠지며, repo의 default/fallback이 참조하면 P1 issue입니다 (H6).
- **초기 profile (survey 근거):**
  - mara/gpt-oss-120b: forced_tool, harmony, `reasoning` field, `reasoning_effort`
  - mara/MiniMax-M3: forced_tool, `chat_template_kwargs.enable_thinking=false`, reasoning field 없이 reasoning_tokens > 0. tool_choice auto일 때 content에 thought가 섞인다는 survey 메모는 아직 recording 근거가 없습니다 (§1.1 b). M1 canary의 auto-mode tool case로 확보해 fixture로 둡니다. 처리 규칙은 §4.4 E (tool_calls가 있으면 content는 `extract.preamble_content`)
  - mara/DeepSeek-V3.1: json_object, tools unreliable (function valid 0.5)
  - mara/DeepSeek-V3.2, mara/gemma-4-31B-it: 모두 `unknown`. onboarding 대상
  - mara/MiniMax-M2.7 (retired): `result_may_live_in_reasoning: true`, `<mm:think>`
  - deepseek: `extra_body.thinking`, reasoning_effort max/high (room:store/llm.py:1237)
  - kimi: `reasoning_content`, temperature clamp 0.6/1.0 (:1255)
  - openai: json_schema_strict, o-series는 `max_completion_tokens`, temperature 생략 (`_uses_openai_reasoning_parameters`)
  - vllm: json_schema → structured_outputs (:1329)
  - default: json_object, floor 2048 (room:llm_structured.py:73 `_DEFAULT`)
- 현재 Python capability registry (room:llm_structured.py:41-73)는 M2에서 profile로 1:1 변환하고 parity test로 고정합니다.
- **Rust code가 필요한 경우는 `ReasoningMarkup` 구현뿐입니다:** harmony channel parser, `<think>`/`<mm:think>` (unterminated 포함) splitter. 새 모델 추가는 JSON 파일 하나와 recording N개로 끝납니다 (C5).

### 4.7 PyO3 surface

```python
from seocho_core import llm_io as lio   # _native 필수. fallback 없음

c = lio.compile_contract(task="graph_extraction", contract_rev=1, exposure="structural",
                         ontology=snapshot_dict)        # -> Contract (opaque)
c.sha256; c.wire_schema("openai_strict"); c.output_section()

plan = lio.plan_request(c, provider="mara", model="gpt-oss-120b",
                        policy={"enforcement": "strict"}, attempt=0)   # -> dict
body = lio.build_request(request_ir, plan)                             # -> dict

r = lio.interpret(c, request=body, status=200, body=raw_bytes,
                  provider="mara", model="gpt-oss-120b", policy={"enforcement": "strict"})
r.verdict; r.value; r.codes; r.usage; r.receipt
lio.next_step(r, budget={"attempts_left": 3, "boosts_left": 1})   # -> {"kind": "retry", ...}

lio.extract_json_object(text)           # oracle 호환 shim (llm_structured.py:132 signature)
```

- `seocho_core/llm_io.py`는 `seocho_core._native`에서 직접 import합니다. `NATIVE_AVAILABLE` fallback (seocho_core/__init__.py:7-22)을 쓰지 않습니다.
  - `SEOCHO_LLM_IO=shadow`: native import 실패 시 shadow를 건너뛰고 `seocho.gen_ai.llm_io.shadow_skipped` metric을 남깁니다. CI와 dashboard에서 이 값이 0인지 assert합니다.
  - `SEOCHO_LLM_IO=rust`: native import 실패는 `ImportError`입니다.
- 경계 비용: `body`는 `bytes`로 받아 복사를 1회만 합니다. 결과 dict는 한 번에 변환합니다. 목표는 호출당 50 µs 미만입니다 (M4 측정).
- GIL: `interpret`는 pure이므로 `py.allow_threads` 안에서 실행합니다.
- 언어 경계 계약: `contract/v1/*.json` golden fixture (contract hash, plan, request body, outcome)를 Rust와 Python 양쪽에서 replay합니다. lifecycle 설계 I12와 같은 방식입니다 (rust:docs/design/rust-lifecycle-authority.md, §5 I12).

---

## 5. Contract 설계

### 5.1 Task 목록과 contract 소유

`TaskKind`는 room:prompt_ir.py:19 `PromptStage`의 상위 집합입니다. task마다 contract family가 하나 있습니다.

| TaskKind | 현재 위치 / schema source | ontology 의존 | 기본 exposure |
|---|---|---|---|
| `graph_extraction` | room:index/extraction_engine.py:94; room:query/strategy.py:75-351 문장 | rule만 (label/type, endpoint) | **Structural** (firewall) |
| `graph_extraction_retry`, `reextract_scored` | extraction_engine.py:122; room:index/pipeline.py:1162 | 같음 | Structural |
| `entity_linking` | extraction_engine.py:150; core.py:1820 | 같음 | Structural |
| `query_intent` | room:query/planner.py:62; room:query/cypher_builder.py:259, :212 | schema_hints (`anchor_label`, `target_*`) | Structural (Vocabulary는 Q1) |
| `query_repair` | planner.py:189 `{cypher, params, strategy}` | 없음 | Structural |
| `text2cypher` | room:query/text2cypher.py:70 `{cypher}` | 없음 (EXPLAIN 검증은 caller) | Structural |
| `grounded_text2cypher` | room:query/grounded_text2cypher.py:41 `{cypher, params}` | 없음 | Structural |
| `query_slots` | room:query/semantic_decompose.py:28 + `_INTENTS` | 없음 (enum 고정) | Structural |
| `t2c_judge` | room:eval/text2cypher_judge.py:20 `_SCHEMA` | 없음 | Structural (기존 schema import) |
| `entity_resolution` | room:qualification_store.py:1309 `{same_entity, confidence, rationale}` | 없음 | Structural |
| `extraction_critique` | room:index/extraction_critique.py:108 | 없음 | Structural |
| `ontoclean` | room:ontology/ontoclean.py:239 | class 목록 (key 집합) | Vocabulary (ontology critique이며 extraction 아님) |
| `ambiguity_proposals`, `fibo_roots` | room:ontology/ambiguity.py:415; room:fibo_catalog.py:290 | label 검증 | Structural + rule |
| `answer_facts`, `answer_judge` | room:evaluation.py:404, :453 | facts label 검사 | Structural + rule |
| `tool_call(name)` | room:eval/agentic_graph_agents.py:152; harness:finder acceptance (`submit_facts`, strict-호환); harness:shared-knowledge agents (`assign_work`, nested object에 `additionalProperties:false` 없음) | 없음 | tool `parameters`가 곧 wire schema. 외부 schema는 §5.2 L1의 normalize 규칙을 거침 |
| `free_text` | room:query/answering.py:139 | 없음 | 없음 (`TextContract`: reasoning leak 검사만) |
| `projection` | seochod 입력 payload (main.rs:87-103) | endpoint label rule | Structural. LLM task가 아니라 seochod contract test용 shape |

### 5.2 `OutputContract`의 세 층

```
OutputContract (seocho.output_contract.v1)
├─ identity: task, contract_rev, exposure, ontology binding, dialect set
├─ L1 wire schema   : JSON Schema subset "js1" (decode-time 강제 + 1차 검증)
├─ L2 lowering      : wire → domain 변환 (결정적, 손실이 있으면 명시)
└─ L3 semantic rules: SHACL 유사 rule (ontology에서 파생, 사후 검증)
```

**L1. JSON Schema subset "js1".** offline에 jsonschema crate가 없으므로 subset을 직접 소유합니다. 범위는 **OpenAI strict json_schema와 vLLM이 둘 다 받는 교집합**입니다. schema 하나로 constrained decoding과 검증을 함께 하려는 것이므로, 이 제약은 결점이 아니라 이 설계의 기능입니다.
- 허용: `type` (object/array/string/integer/number/boolean/null), `properties`, `required`, `additionalProperties: false`, `items`, `enum`, `const`, `minItems`/`maxItems`, `maxLength`, `anyOf` (1단, discriminator `const` 필수), 비재귀 `$defs`/`$ref`, `description`.
- 금지: `pattern`, `format`, 재귀 `$ref`, `oneOf`/`allOf`, open object. 금지 keyword는 compile error `contract.unsupported_keyword`입니다.
- **외부 tool schema** (기존 실험과 room의 tool 정의)는 거부하지 않고 load 시점에 **normalize**합니다. `additionalProperties`가 없는 object에는 `false`를 넣고, `required`가 일부만 있으면 `ToolParameters` dialect에서는 원본 `required`를 유지하고 `OpenAiStrict` lowering에서만 전체 required + 선택 property의 `null` 허용으로 바꿉니다. 바꾼 지점마다 `contract.tool_schema_normalized{path}` (Info)를 남기고, normalize 결과가 contract hash 입력입니다. 예: `assign_work` (harness:shared-knowledge agents)는 nested `agents.items`에 `additionalProperties:false`가 없으므로 normalize 대상입니다. `pattern`/`format`처럼 의미가 있는 금지 keyword만 계속 compile error입니다. validation 쪽에서 normalize가 원래보다 엄격해지는 부분 (원본은 extra key 허용)은 `schema.additional_property`를 Degrade로 낮춥니다.
- `CompiledSchema`는 path table을 미리 계산한 tree입니다. 검증은 payload 크기에 선형이고 regex를 쓰지 않습니다.

**L2. Lowering (wire ≠ domain).** OpenAI strict는 open object를 금지합니다. 그래서 extraction의 `properties` map은 wire에서 `[{"name","value"}]` 배열로 표현하고, lowering이 domain의 map으로 되돌립니다. lowering 단계는 고정 enum입니다.
- `PairsToMap { path }`
- `WrapBareArray { into, by_triple_keys }`: 현재 `normalize_payload` (extraction_engine.py)의 heuristic을 그대로 옮깁니다 (subject/object, source/target, from/to, head/tail).
- `TriplesToRelationships`: nodes+triples 변형 (strategy.py)
- `ParseStringifiedJson { path }`: tool `function.arguments`. 값이 이미 object/array이면 parse하지 않고 통과시키며 `lower.args_object` (Info)를 냅니다 (§4.6 `tool_args_encoding`)
- `RenameKey { from, to }`

lowering 결과는 domain type입니다. `GraphPayload { nodes: Vec<Node>, relationships: Vec<Relationship> }`는 seochod `Node`/`Relationship` (main.rs:87-103)과 field가 같습니다. 첫 deliverable에서는 `seocho-contract`에 자체 정의를 두고 seochod test fixture를 golden으로 맞춥니다. seochod의 private type을 공유 rlib으로 추출해 `projection` shape과 같은 Rust type에서 생성하는 것은 seochod 의존성 변경이므로 후속 작업입니다 (§1.5, §9 M5+).

**L3. Semantic rules.** ontology에서 파생하고 평가 순서는 고정입니다.

| rule kind | 근거 | 파생 source |
|---|---|---|
| `closed_label{allowed, generic_entity: Allowed\|Forbidden}` | validate_extraction closed (core.py:1977), `_NO_GENERIC_ENTITY_CLAUSE` (extraction_engine.py:30-50 부근) | node label, `Entity` 선언 여부 |
| `closed_rel_type{allowed}` | 같음 | relationship type |
| `endpoint_label{rel, source, target, any_wildcard, broader_closure}` | core.py:1977 docstring (Any, exact, broader subsumption) | source/target + broader chain을 compile 시 펼친 closure |
| `no_dangling_endpoint` | 같음 | 상수 |
| `required_property` / `datatype` / `enum` / `range` | rules.rs:123 kind 4종 | `infer_rules_from_nodes` 출력 또는 ontology property 선언 |
| `unique_node_id` | seochod MERGE key `{id, _workspace_id}` (main.rs:420) | 상수 |
| `bounds{max_nodes, max_rels, max_string}` | seochod 입력 상한 | 설정 |

### 5.3 Exposure policy와 extraction firewall

decode-time schema에 vocabulary (label enum)를 넣으면 모델 출력 분포가 바뀝니다. prompt에 richness를 넣는 것과 같은 범주의 개입입니다.

- `Exposure::Structural`: wire schema가 ontology와 무관합니다. label/type은 `string` (`maxLength`만)입니다. `graph_extraction`, `graph_extraction_retry`, `entity_linking`의 기본값입니다. **불변식 C3:** Structural wire schema bytes와 OUTPUT_SCHEMA text는 ontology와 무관하게 같습니다. `_STRICT_VOCABULARY_GUIDANCE`가 상수인 것 (extraction_engine.py:30-40)과 같은 원리의 decode-time 판입니다.
- `Exposure::Vocabulary` (**후속**, 첫 deliverable에서는 enum variant만 예약하고 compile은 `contract.exposure_not_enabled`로 거부): label/type을 `enum`으로 노출합니다. 명시적 opt-in, ADR, 사전 등록 실험 gate가 필요합니다 (Q1). exposure는 contract identity에 포함되므로 다른 hash가 나오고, 실험 arm이 receipt에서 구분됩니다.
- prompt에 렌더링되는 OUTPUT_SCHEMA section도 contract의 wire schema에서 생성합니다. strategy.py의 손으로 쓴 shape 문장과 실제 검증 기준이 어긋나는 일이 사라집니다.

### 5.4 canonical JSON과 hash

**canonical JSON.** Python `_stable_json` (context.py:42-43)과 byte 단위로 같아야 합니다: `json.dumps(sort_keys=True, separators=(",",":"), default=str)`.
- `ensure_ascii=True`가 기본이므로 non-ASCII는 `\uXXXX`, astral 문자는 surrogate pair로 escape합니다. float는 Python `repr`과 같아야 합니다.
- `seocho-core`의 `PyValue`/`write_py_json`을 `seocho-contract::canon`으로 옮기고 separator와 key 정렬을 parameter로 만듭니다 (§1.5). rule inference는 기존 기본 separator로, contract hashing은 compact + sorted로 호출합니다.
- 입력 snapshot에서 비JSON 타입을 금지합니다. `default=str` 경로는 exporter가 미리 문자열화합니다.

**hash 3종.**

| 이름 | 입력 | 용도 |
|---|---|---|
| `contract_sha256` (64 hex) | `{schema_version: "seocho.output_contract.v1", task, contract_rev, exposure, ontology_context_hash, rdf_bundle_sha256, wire_schema (canonical), lowering, rules, crate_semver_major}` | receipt, trace, corpus key |
| `wire_schema_sha256[dialect]` (64 hex) | dialect별 lowering된 wire schema (`openai_strict`, `vllm`, `tool_parameters`, `json_object_prompt`) | provider cache key, 요청 재현 |
| `profile_sha256` (64 hex) | model profile JSON (canonical) | interpretation receipt |

- `ontology_context_hash`는 16 hex로 잘린 값입니다 (`_hash_payload`, context.py:46-47). seochod `valid_digest`는 64 hex를 요구합니다 (main.rs:231-233). contract는 둘 다 **그대로** 포함하고 재해석하지 않습니다. 64-hex anchor는 `rdf_bundle_sha256`입니다 (room:ontology/rdf_bundle.py:63, :151-152).
- ontology가 없는 task (`query_slots` 등)는 binding field가 `null`이고 hash는 ontology와 무관하게 안정적입니다.

### 5.5 receipt chain 연결 (seochod 변경 없음, 후속 M5+)

이 절은 첫 deliverable 범위가 아닙니다. M1-M4에서는 3번의 호출 단위 receipt만 recording의 `live_outcome`과 trace attribute로 씁니다. 1, 2, 4, 5는 parse/repair loop가 최소 한 task에서 authoritative가 된 뒤 진행합니다.


1. 현재 `canonical_projection_receipt.v1` (room:ontology/projection_receipt.py:80-99)은 agent profile 전체를 sha256하여 `agent_profile_sha256`을 만듭니다. profile에는 이미 `ontology_context_hash`가 있습니다 (:95).
2. M5에서 projection agent profile에 `output_contracts: {task: contract_sha256}`와 `extraction_policy: {mode, exposure, salvage}`를 추가합니다. 그러면 `projection_receipt_sha256`이 contract를 전이적으로 고정합니다. seochod는 기존대로 64-hex 형식과 bundle 일치만 검사합니다 (main.rs:955-971, :261-262).
3. 호출 단위 receipt `seocho.llm_interpretation.v1`: `{contract_sha256, wire_dialect, profile_sha256, provider, model, raw_response_sha256, verdict, codes[], value_sha256, usage}`. trace span attribute로 남기고 recording의 `live_outcome`에도 들어갑니다. seocho-fix.11 (per-call outcome, reasoning/output token split)을 채웁니다.
4. contract binding의 `ontology_context_hash`가 `RunContext.ontology_context_hash` (room:ontology/run_context.py:186)와 다르면 `contract.binding_mismatch`로 호출 전에 fail closed합니다. 기존 `same_ontology_context_hash` (context.py:722) drift 검사는 그대로 둡니다.
5. (선택) node property `_output_contract_sha256` stamp. seochod `props_send` (main.rs:189-217) 확장이 필요하므로 Q2로 남깁니다.

---

## 6. Diagnostic taxonomy

`Diag = { code: Code, layer: Layer, disposition: Disposition, path: Option<JsonPointer>, detail: ShortString }`. `detail`은 200자 이하이고 원문을 포함하지 않습니다. trace에는 code만 남기고 원문은 corpus에만 둡니다 (H5의 privacy 측면).

`Disposition = Info | Degrade | Retryable(RetryHint) | Terminal`. `next_step`은 diag의 disposition과 `RetryBudget`만 보고 결정합니다. 한 attempt에 여러 diag가 있으면 아래 **우선순위 class 중 가장 높은 것 하나**가 next_step을 정합니다 (전순서, 같은 class 안에서는 table 순서).

| layer | code (안정 문자열) | disposition | `next_step` | 근거 |
|---|---|---|---|---|
| provider | `provider.auth` (401/403) | Terminal | `Stop` (run 전체) | H1 |
| | `provider.payment_required` (402) | Terminal | `Stop` 또는 `FallbackModel` | sr-z5g |
| | `provider.rate_limited` (429) | Retryable | `Retry` + 지수 backoff, 최대 2회 | seocho-c2a5, zpoj |
| | `provider.upstream` (408/500/502/503/504) | Retryable | `Retry` + backoff | |
| | `provider.json_truncation_400` | Retryable | `Retry{MaxTokens(max(2x, 16384))}` 1회 | room:store/llm.py:663-664 의미 보존 |
| | `provider.output_limit_400` | Retryable | `Retry{MaxTokens}` 1회 | gpt-oss 22/340 |
| | `provider.tool_choice_unsatisfied` | Retryable | `Retry{Temperature(0.3)}` → `0.6` → `FallbackModel` | sr-z5g, harness:graphcot recovery ("did not complete") |
| | `provider.tool_choice_wrong_tool` | Retryable | 같음 | M3 6/318 |
| | `provider.unclassified_4xx` (401/402/403 이외 status에서 어느 pattern과도 안 맞음) | **Retryable 아님, Terminal도 아님 (기본)**: 해당 논리 호출만 `FallbackModel` (없으면 그 호출 실패), run은 계속. canary에서는 Info 신호 + issue (§7.5). profile이 `unclassified_4xx: terminal`을 명시한 경우만 Terminal | `FallbackModel` 또는 호출 실패 | format drift 감지. 문구 drift가 run 전체를 멈추지 않게 함 |
| transport | `transport.body_not_json`, `transport.no_choices` | Terminal | `Stop` | |
| | `transport.timeout`, `transport.connect` | Retryable | `Retry` + backoff | |
| | `transport.finish_length` | Retryable / Degrade | `Retry{MaxTokens}` | M3 15/2298 |
| | `transport.content_empty_reasoning_only` | strict: Retryable, 그 외: Degrade | strict: `Reprompt` | ub5, H2 |
| | `transport.textual_tool_markup` | Retryable | `Reprompt` | agentic_graph_agents.py:187 |
| | `transport.unknown_wire_shape` | Info (format detector) | — | §7.5 |
| | `transport.usage_reasoning_tokens_missing` | Info | — | fix.11 |
| extract | `extract.fence`, `extract.think{kind}`, `extract.prose_trimmed`, `extract.candidate_selected` | Info (→ Salvaged) | — | |
| | `extract.reasoning_carrier` | Degrade (strict에서 Terminal 승격 가능) | — | H2, C7 |
| | `extract.no_json` | Retryable | `Reprompt` 1회 | |
| | `extract.tool_args_invalid_json` | Retryable | `Reprompt` | |
| parse | `parse.repaired{kind}`, `parse.truncated_closed` | Degrade (→ Repairable) | `Reprompt` 또는 `Retry{MaxTokens}` | |
| | `extract.preamble_content` | Info | — | tool_calls와 같이 온 content (§4.4 E) |
| lower | `lower.bare_array_wrapped`, `lower.triple_keys_coerced`, `lower.stringified_args`, `lower.args_object` | Info | — | normalize_payload, §4.6 |
| schema | `schema.missing_required`, `schema.type`, `schema.additional_property`, `schema.enum`, `schema.max_items` | Retryable | `Reprompt` 최대 1회 (semantic_decompose.py:83 일반화) | |
| ontology | `ontology.unknown_label`, `ontology.unknown_rel_type`, `ontology.endpoint_label`, `ontology.dangling_endpoint`, `ontology.required_property`, `ontology.datatype`, `ontology.enum`, `ontology.range` | policy에 따라 Terminal / Degrade / Info | strict: `Reprompt` 1회 | enforcement.py:18 |
| contract | `contract.binding_mismatch`, `contract.unsupported_keyword`, `contract.exposure_not_enabled` | Terminal (호출 전) | `Stop` | §5.3-5.5 |
| | `contract.tool_schema_normalized{path}` | Info (compile 시) | — | §5.2 L1 |
| ops | `ops.profile_unmatched` | Info (format detector) | default profile 사용 | §7.5 |

**next_step 우선순위 (전순서).** Reprompt는 이전 출력을 prompt에 붙이므로 budget 소진을 고치지 못하고 오히려 악화시킵니다. 그래서 길이 계열이 reprompt 계열보다 항상 앞섭니다.

| 순위 | class | 포함 code | 결과 |
|---|---|---|---|
| 1 | Terminal | `provider.auth`, `provider.payment_required`, `transport.body_not_json`, `transport.no_choices`, `contract.*`, strict 승격된 code | `Stop` / `FallbackModel` |
| 2 | 전송 재시도 | `provider.rate_limited`, `provider.upstream`, `transport.timeout`, `transport.connect` | `Retry` + backoff (body 무시) |
| 3 | budget/길이 | `transport.finish_length`, `provider.output_limit_400`, `provider.json_truncation_400`, `parse.truncated_closed` (finish=length와 함께일 때) | `Retry{MaxTokens}`. boost budget이 없으면 `FallbackModel` 또는 실패. **이 class가 있으면 4, 5 class code는 next_step에 쓰지 않음** |
| 4 | provider mode 거부 | `provider.tool_choice_unsatisfied`, `provider.tool_choice_wrong_tool`, mode 거부 계열 (`Downgrade` 근거) | `Retry{Temperature}` → `FallbackModel` / `Downgrade` |
| 5 | reprompt | `transport.content_empty_reasoning_only`, `transport.textual_tool_markup`, `extract.no_json`, `extract.tool_args_invalid_json`, `parse.repaired`, `schema.*`, `ontology.*` (strict) | `Reprompt` (최대 1회) |
| 6 | 비재전송 | `provider.unclassified_4xx` (기본), Degrade, Info | `FallbackModel`/실패 또는 `Accept` |

- 1 > 2 > … > 6입니다. 낮은 class의 code도 diag와 metrics에는 모두 남습니다. 결정에만 쓰지 않습니다.
- chain fixture (M1 T1 필수): (a) MiniMax-M2.7 `finish=length` + content 빔 + reasoning에 partial JSON → `finish_length`, `content_empty_reasoning_only`, `parse.truncated_closed`가 함께 나와도 `Retry{MaxTokens}`. (b) forced tool call이 `finish=length`에서 잘려 arguments가 잘린 JSON → `extract.tool_args_invalid_json` + `finish_length`이어도 `Retry{MaxTokens}`. (c) 429와 schema 위반이 같은 run의 다른 attempt에서 나는 경우 각 attempt가 독립 판정.

- `Downgrade`는 retry ladder의 strip 단계를 대체합니다. profile이 허용하고 근거 code가 `provider.*`의 mode 거부 계열일 때만 냅니다. 근거 없는 "일단 response_format을 빼고 다시"는 하지 않습니다. 43k-char runaway prose의 원인이었기 때문입니다 (seocho-ub5 v4).
- code 집합은 `contract/v1/codes.json`으로 commit합니다. 제거나 의미 변경은 major bump이고 추가는 minor입니다 (`#[non_exhaustive]`). code 추가는 PR review 대상입니다.

---

## 7. Conformance harness (record/replay over MARA)

### 7.1 Recording: `seocho.llm_exchange.v1` (HTTP attempt당 JSONL 1줄)

`sk-llm/1` (harness:shared-knowledge `RRTransport`)의 상위 집합입니다. 추가되는 것은 (a) response의 **raw bytes**와 그 fidelity 표시, (b) **full request body** (private tier), (c) contract, profile, case와의 연결, (d) run 단위 replay identity입니다.

```json
{
  "schema_version": "seocho.llm_exchange.v1",
  "rec_id": "sha256:<source.run_id||chain.logical_call_id||chain.attempt||response.body_sha256>",
  "captured_at": "2026-10-25T06:00:12Z",
  "source": {"kind": "canary|sweep|onboard|prod-shadow|import:sk-llm/1|import:finder-v1|import:room-diagnostics|synthetic|synthetic-derived",
             "run_id": "...", "git_sha": "...", "llm_io_version": "0.1.0",
             "wire": true, "origin": null},
  "endpoint": {"provider": "mara", "host": "api.cloud.mara.com", "path": "/v1/chat/completions", "catalog_snapshot": "sha256:..."},
  "model": "gpt-oss-120b",
  "case": {"case_id": "graph_extraction/finder-0007-long", "task": "graph_extraction", "input_class": "public",
           "contract_sha256": "<64>", "wire_dialect": "tool_parameters", "profile_sha256": "<64>", "enforcement": "strict"},
  "chain": {"logical_call_id": "...", "attempt": 0, "next_step_of": null, "step_kind": "initial|retry|reprompt|downgrade|fallback|sdk_retry"},
  "request": {"body": {"model": "...", "messages": [], "tools": [], "tool_choice": {}, "max_tokens": 8192, "temperature": 0.0},
              "req_hash": "sha256:...", "occurrence": 0},
  "response": {"http_status": 200, "headers": {"content-type": "application/json", "x-request-id": "..."},
               "body_fidelity": "raw", "body_utf8": "<exact bytes>", "body_b64": null, "body_sha256": "sha256:...",
               "transport_error": null},
  "timing": {"latency_ms": 2312},
  "live_outcome": {"verdict": "Salvaged", "codes": ["extract.think", "extract.fence"], "fingerprint": "sha256:..."},
  "redaction": {"policy": "rec-redact/1", "applied": ["auth_header_dropped"], "replay_class": "full", "replayable": true}
}
```

- **raw bytes 규칙:** UTF-8로 유효하면 `body_utf8`, 아니면 `body_b64`를 씁니다. `serde_json::Value`로 parse했다가 다시 쓰는 것을 금지합니다 (C10). key 순서, 중복 key, 숫자 표기가 parser 동작에 영향을 줄 수 있고, serde_json 기본 `Map`은 정렬되는 `BTreeMap`이라 원래 순서도 잃습니다.
- **`body_fidelity: raw | reserialized`.** 기존 corpus에는 raw bytes가 없습니다 (§1.4: sk-llm/1은 parsed dict, finder-v1은 `clean()` 후 parsed dict). importer는 이것들을 다시 직렬화하므로 `reserialized`로 표시하고, `body_sha256`은 "재직렬화한 bytes 기준"이라는 뜻이 됩니다. 규칙:
  - C10과 byte 의존 fixture (key 순서, 중복 key, 숫자 표기, 비JSON body 잘림)는 `raw` recording만 근거로 씁니다.
  - `reserialized`는 T/E/P의 구조 판정 (carrier, finish, error 분류, tool args 문자열)에는 씁니다. importer는 원본 파일 경로와 원본 파일 sha256을 `source.origin`에 남깁니다.
  - 비JSON body가 4,000자로 잘린 sk-llm/1 row는 `body_fidelity: reserialized, truncated: true`이고 transport 분류에만 씁니다.
- **RRTransport 선행 수정 (M1 PR-1의 일부).** M1 import보다 먼저 RRTransport가 `upstream.read()`의 원본 bytes를 `response.body_b64` (또는 `body_utf8`)로 저장하도록 바꿉니다. 기존 `body` dict는 하위 호환을 위해 같이 남기되 replay는 raw bytes가 있으면 그것을 그대로 돌려줍니다. 그러면 M1 canary 데이터는 첫날부터 `raw`입니다.
- **SDK 재시도 차단.** recorder와 M4 shadow, M5 driver의 OpenAI client는 `max_retries=0`이 **필수**입니다. 불가능한 경로 (Agents SDK 등)는 transport 계층에서 HTTP attempt마다 recording하고 `chain.step_kind: "sdk_retry"`로 표시합니다. 이 둘 중 하나가 없으면 chain 검사 (§7.2)가 SDK 내부 재시도를 설명하지 못하므로 해당 경로의 recording은 chain 검사에서 제외됩니다.
- **`req_hash`:** sk-llm/1 recorder의 HASH_FIELDS (harness:shared-knowledge)에 `extra_body`, `chat_template_kwargs`, `reasoning_effort`를 더합니다. ISO timestamp/`_writer_ts` scrubber는 **hash 입력에만** 적용합니다.
- **`live_outcome`:** 기록 시점의 core가 낸 판정입니다 (M1 import 시에는 Python oracle 판정을 `oracle_outcome`으로 따로 넣습니다). replay self-consistency의 기준입니다.

**redaction 정책 `rec-redact/1`**

| 대상 | private tier | committed tier |
|---|---|---|
| `Authorization` header, API key | 기록 안 함 | 기록 안 함 |
| response header | **allowlist만**: `content-type`, `x-request-id`. 나머지 (`set-cookie`, account/org 식별자 포함)는 기록 안 함 | 같음 |
| body 안의 Bearer token, `sk-…` | sk-llm/1 recorder의 regex (`exchange_v1.BEARER_RE`/`SK_RE`)로 치환하고 `replayable:false`, `replay_class: none` | 같음. 치환이 일어난 recording은 committed 불가 |
| reasoning field, think/harmony 구간 | **보존** | **보존** (`input_class`가 public/synthetic인 case만 committed 가능) |
| prompt (messages) | 보존 | public dataset (FinDER, GRBench 등)이나 synthetic 입력일 때만 |
| 402 body의 `billing_portal_url`, `balance_units` | 보존 | `[redacted]`로 치환하고 `replayable:false` |

- 원칙: **입력 분류로 해결하고 출력은 고치지 않습니다.** committed tier 여부는 case manifest의 `input_class`로 결정하고 CI가 검사합니다.
- **replay class.** response body를 바꾼 recording은 원칙적으로 `replayable:false`입니다. 예외를 하나 둡니다.
  - `replay_class: full`: body가 변형되지 않음. 모든 replay 검사 대상.
  - `replay_class: carrier_intact`: carrier 바깥의 field만 치환됨 (`message.reasoning`, `usage.completion_tokens_details.reasoning_tokens`, `error_model_output`). replay 시 fingerprint 범위를 **`tool_calls`와 `content` carrier, finish, HTTP 분류**로 좁힙니다. reasoning carrier 판정, reasoning token metric, `error_model_output` 기반 분류는 이 class에서 검사하지 않습니다. `redaction.applied`에 치환된 JSON pointer 목록을 남깁니다.
  - `replay_class: none` (`replayable:false`): carrier 자체가 치환됐거나 secret scrub이 일어남. metrics 전용.
- **finder-v1**은 `carrier_intact`로 import합니다. 단 `clean()`의 문자열 regex (think/harmony 구간 치환)가 content 또는 tool arguments 문자열에 실제로 적용된 row (`[private reasoning omitted]` 포함)는 `none`입니다. finder-v1에는 reasoning_tokens와 `error_model_output`이 **없다**는 사실을 import manifest에 명시하고, reasoning token baseline은 GraphCoT JSONL과 새 canary에서만 냅니다.
- secret scan은 CI gate입니다. committed tier에 Bearer/`sk-` 패턴이 남아 있거나 scrub이 적용된 recording이 있으면 실패합니다.
- private 입력에서 나온 중요한 failure 형태는, 같은 형태를 합성 입력으로 재현한 `synthetic-derived` recording으로 committed tier에 넣습니다. 이 recording은 live 비율 집계에서 제외합니다.

**corpus 배치**

```
data:llm-corpus/                                   (private, 0700)
  MANIFEST.json                                    corpus_version, shard 목록 + sha256
  cases/<task>/<case_id>.json                      RequestIr 입력, input_class, ontology ref, contract_sha256
  rec/<YYYY-MM>/<provider>/<model>/<task>.jsonl    append-only 관측 행. rec_id는 run 단위라 반복 관측도 별도 행
  bodies/<sha256[0:2]>/<sha256>                    content-addressed body (같은 bytes는 1회만 저장. 관측은 dedupe하지 않음)
  imports/{sk-llm-1,finder-v1,room-diagnostics}/   importer 출력 (원본 경로와 sha 기록)
  expect/<llm_io_major>/<task>/<case_id>/<model>.json   blessed {verdict, codes, fingerprint, rec_id}
  regressions/                                     판정이 바뀐 live row (fixture 승격 후보)
  metrics/<YYYY-MM-DD>/<run_id>.jsonl              seocho.llm_metrics.v1
  ledger/budget.json                               BudgetGuard ledger (file-locked)
  tombstones.jsonl                                 잘못 들어간 recording과 사유

rust:crates/seocho-llm-conformance/corpus/         (committed subset, 같은 layout)
```

- **불변성:** body는 content-addressed이고 관측 행은 append-only입니다. 둘 다 지우거나 고치지 않습니다.
- **관측과 body를 분리합니다.** T=0 반복 (MARA에서 흔함)으로 같은 request와 같은 body가 여러 run에서 나와도 관측 행은 각각 남습니다. rate와 Wilson 신호 (§7.5)는 관측 행으로 계산합니다. 중복 제거는 body 저장에만 적용합니다.
- **committed subset 상한:** recording 2,000개, 20 MB (추정 평균 body 4-8 KB + request). 이 상한이 G5의 2 s를 지탱합니다.
- **seed 출처:**

| source | 수량 | 쓸 수 있는 범위 |
|---|---|---|
| harness:shared-knowledge의 live smoke / synthetic recording | 38 + 56 | `reserialized`. T/E/P 구조 판정과 committed 후보. byte 의존 fixture와 C10 근거로는 불가. request는 `messages_sha256`뿐이므로 request replay는 불가 |
| finder-v1 http-state + receipts (data:) | 4,050 | `reserialized`, `carrier_intact` (일부 `none`). tool args carrier / 400 status 분류. reasoning, reasoning_tokens, `error_model_output` 없음. private |
| 로컬 MARA diagnostics (`.seocho/diagnostics/`, untracked) 두 묶음 | 약 170 | gemma, M2.7, DeepSeek-V3.1이 들어 있는 유일한 출처. private |
| harness:graphcot run JSONL | — | raw body 없음. latency/token baseline으로만 metrics에 import |

### 7.2 replay runner

- replay key는 `(run_id, logical_call_id, attempt)`이고 lookup은 **run 단위로 scope**됩니다. RRTransport의 run 내부 규칙 (`(agent, req_hash)` + occurrence)은 `logical_call_id`가 없는 import 데이터에서만 쓰며, 그 경우 key는 `(run_id, agent, req_hash, occurrence)`입니다. 여러 run에 걸친 `(model, req_hash, 0)` 같은 전역 key는 쓰지 않습니다. 없으면 **ReplayMiss = fail closed**이고 lenient mode는 test에서 금지합니다.
- recording마다 세 가지를 검사합니다.
  1. **self-consistency:** `interpret(rec).fingerprint == rec.live_outcome.fingerprint`. `llm_io_version`이 같은 recording에 한합니다.
  2. **expectation:** `expect/<major>/…/<model>.json`의 fingerprint와 비교합니다.
  3. **chain:** `next_step`의 계획이 기록된 다음 attempt (`chain.next_step_of`)의 request 변화와 일치하는지 검사합니다. retry 정책 회귀를 잡습니다. 한 논리 호출의 HTTP attempt 수가 `next_step`이 계획한 수와 정확히 같아야 합니다. `max_retries=0` 경로에서 `sdk_retry` attempt가 나오면 실패입니다 (H1).
- **bless:** parser 변경으로 fingerprint가 바뀌면 `llmconf bless`가 old/new verdict, code, value diff를 보여주고 사람이 승인합니다. 승인 commit 메시지에 바뀐 case 수를 넣습니다.
- 실패 메시지에는 `rec_id`와 재현 명령 `llmconf replay --rec <id>`를 넣습니다.

### 7.3 검사 계층과 속도 예산

| 계층 | 무엇을 | 언제 | 예산 | network |
|---|---|---|---|---|
| **T0** unit | decode/extract/validator 단위 test, profile lint, codes.json 정합성 | 매 `cargo test` | < 0.3 s | 없음 |
| **T1** committed replay | committed corpus 전체 `interpret` + expectation + chain | 매 `cargo test`, pre-push | **< 2 s debug** (≥ 1,000 rec/s. 8 KB body의 serde_json parse는 release 20-80 µs, debug 수백 µs로 추정) | 없음 |
| **T2** private replay + oracle | private corpus 전체 replay, Python oracle diff, seeded mutation 1만 회, metrics 재계산 | nightly, parser PR | Rust **< 30 s release**, oracle **< 5 min** | 없음 |
| **T3** catalog probe | `GET /v1/models` diff | hourly | 0 token | MARA |
| **T4** canary | contract × (짧은, 긴 입력) × catalog 모델 (§7.4) | 짧은 입력 하루 4회, 긴 입력 하루 1회 | ≤ $1.1/day (실측 p95 기준 상한) | MARA |
| **T5** sweep | 모든 contract × case 10개 × 모델 (V3.2는 월 1회) | 주 1회 | ≤ $3.5/run | MARA |
| **T6** onboard probe | 새 모델/형식 변화 probe suite | 이벤트 시 | ≤ $0.5 | MARA |

- `llmconf check`는 개발자용 "interface가 아직 맞는가" 명령입니다. T0과 T1을 묶어 2 s 안에 끝냅니다.
- 속도 회귀: test 안에서 시간을 assert하면 flaky하므로 하지 않습니다. `llmconf replay --stats`가 rec/s를 출력하고, nightly 값이 baseline보다 30% 이상 느리면 P3 issue를 만듭니다. interpret p99 예산 (body ≤ 64 KiB에서 200 µs, release)은 `#[ignore]` benchmark로 측정합니다 (criterion이 offline에 없으므로 `std::time::Instant` 기반이며 conformance crate에만 둡니다).

### 7.4 canary 구성, 비용, 안전 장치

- **case:** contract 8개 (`graph_extraction`, `entity_linking`, `query_intent`, `text2cypher`, `query_slots`, `t2c_judge`, `free_text`, `tool_call(submit_facts)`) × 2개 (짧은 입력, 긴 입력). 긴 입력은 seocho-ub5의 long-transcript truncation 재현용입니다. 모든 case는 `input_class: public|synthetic`이므로 canary recording은 committed tier 후보입니다.
- **cadence:** 짧은 입력은 하루 4회, 긴 입력은 **하루 1회**입니다. DeepSeek-V3.2는 짧은 입력도 하루 1회입니다. M1 첫 deliverable의 canary는 이 중 `graph_extraction`, `query_slots`, `tool_call(submit_facts)`, `free_text` 4개 contract로 시작하고 contract가 추가될 때 늘립니다.
- **모델:** live catalog 중 `status: active` profile. 402나 연속 429로 `Stop` 상태인 모델은 건너뛰고 skip을 metric으로 남깁니다.
- **token 가정은 실측 p95입니다** (r1 이전의 "completion 800" 가정은 낙관적이었습니다). completion은 reasoning을 포함한 값입니다 (gpt-oss는 `completion_tokens`에 reasoning이 포함됨. GraphCoT 예: 54 중 37).

| 출처 (실측) | 모델 | n | completion p50 / p95 | prompt p50 / p95 |
|---|---|---|---|---|
| finder-v1 http-state (forced tool, extraction/answer) | gpt-oss-120b | 1,945 | 160 / 1,788 | 1,348 / 7,015 |
| | MiniMax-M3 | 1,982 | 83 / 1,535 | 1,323 / 6,133 |
| GraphCoT v2 (tool auto, 짧은 turn) | gpt-oss-120b | 2,181 | 98 / 290 (reasoning p95 274) | 783 / 1,159 |
| | MiniMax-M3 | 2,299 | 77 / 280 (reasoning p95 108) | 1,289 / 2,233 |

  - 계산 가정: **모든 호출이 completion 1,800 (finder-v1 p95 최대)**, prompt는 짧은 입력 2,500 / 긴 입력 7,000 (finder-v1 prompt p95). DeepSeek-V3.1/V3.2와 gemma는 실측이 없으므로 같은 1,800을 쓰고 M3 2주 운영 후 실측으로 바꿉니다. 모든 호출에 p95를 쓰는 것이 retry/reprompt 추가분을 덮는 보수적 상한입니다 (p50은 p95의 1/10 수준).

| 모델 | $/M in | $/M out | 짧은 입력 $/호출 | 긴 입력 $/호출 |
|---|---|---|---|---|
| gpt-oss-120b | 0.15 | 0.75 | 0.0017 | 0.0024 |
| MiniMax-M3 | 0.60 | 2.40 | 0.0058 | 0.0085 |
| DeepSeek-V3.1 | 0.60 | 1.70 | 0.0046 | 0.0073 |
| DeepSeek-V3.2 | 3.00 | 4.50 | 0.0156 | 0.0291 |
| gemma-4-31B-it | 0.38 | 1.15 | 0.0030 | 0.0047 |

  - 짧은 입력, V3.2 제외 4모델: 8 case × $0.0151 = $0.12/run, 하루 4회 $0.48.
  - 짧은 입력, V3.2: 8 × $0.0156 = $0.12/day.
  - 긴 입력, 5모델 하루 1회: 8 × $0.052 = $0.42/day.
  - canary 합계 약 $1.02/day, **월 약 $31**.
  - sweep: 20 contract × 10 case, 짧은 입력, V3.2 제외 주 1회 ($3.0/주, 월 약 $13) + V3.2 월 1회 ($3.1). 월 약 $16.
  - **합계 월 약 $47 (보수적 상한)**. G6 목표 $60 안이고, 월 $100 hard cap까지 여유가 2배입니다. M3 2주 운영으로 실측 (모델별 completion+reasoning p95, attempts_per_call)해 이 표를 갱신하고, 실측이 $60을 넘으면 긴 입력을 격일로 줄입니다.
- **BudgetGuard 이식** (harness:shared-knowledge). file-locked ledger에 cap 세 개를 둡니다.
  - run별 request cap (canary 기본 120. `LIVE_CAP`/`WORKFLOW_CAP` 개념, harness:shared-knowledge config)
  - 일별 USD cap ($5)
  - 월별 USD hard cap ($100)
  - cap을 넘으면 run은 즉시 멈춥니다. 판정은 기록된 usage × catalog pricing으로 합니다.
- **status별 harness 정책:** `next_step` table을 그대로 씁니다. 추가로 402는 해당 모델을 24 h 동안 skip하고, 401은 run 전체를 중단하고 issue를 만들고, 429가 나오면 concurrency를 1로 낮춥니다.
- **concurrency:** 기본 2 (MARA 429/502 at concurrency, seocho-zpoj).
- **timeout:** 900 s를 명시합니다 (ProviderSpec 기본값). H6의 120 s 불일치를 harness에서는 피합니다.
- **key:** `MARA_API_KEY` env 또는 `--credential-file`만 받습니다. 값은 로그에 쓰지 않습니다.

### 7.5 회귀 판정과 bd 자동 issue

**metrics 행 `seocho.llm_metrics.v1`.** key는 `(run_id, provider, model, task, structured_mode, profile_version, llm_io_version)`입니다.

| field | 정의 |
|---|---|
| `n`, `n_ok`, `n_salvaged`, `n_repaired`, `n_retried`, `n_failed` | 논리 호출 (chain) 기준. chain outcome = 마지막 attempt의 verdict + reprompt 여부 |
| `attempts_per_call` p50/p95 | chain 길이 |
| `code_counts{code: n}`, `http_status_counts` | attempt 기준 |
| `tokens.{prompt,completion,reasoning,cached}` p50/p95/sum | 정규화 usage |
| `latency_ms`, `ttft_ms` p50/p95 | client 측정 + SambaNova usage |
| `finish_length_rate`, `reasoning_only_rate` | |
| `cost_usd` | catalog snapshot pricing × token |
| `oracle {agree, rust_better, rust_worse, value_diff, uncovered}` | nightly diff만. 정의는 §7.7 결정 table. `oracle_coverage`는 (model, task)별 covered 비율 |

**판정 규칙** (model × task 단위, 최근 window와 직전 7일 baseline 비교):

| 신호 | 조건 | priority | type |
|---|---|---|---|
| 새 code / `transport.unknown_wire_shape` / `ops.profile_unmatched` / `provider.unclassified_4xx` | 처음 관측 (n ≥ 1) | P1 | bug |
| ok+salvaged 비율 하락 | 현재의 Wilson 95% 하한이 baseline 점추정보다 10%p 넘게 낮음, n ≥ 8 | P1 (≥ 25%p) / P2 | bug |
| salvaged 비율 상승 (ok → salvaged 이동) | 같은 검정, 15%p | P2 | task |
| catalog 모델 추가 | `/models` diff | P2 | task (onboarding) |
| catalog 모델 제거 | diff, 그리고 repo의 default/fallback/profile이 그 id를 참조 | P1 | chore |
| 402 / 401 | 1회 | P1 | chore |
| p95 latency, TTFT, reasoning_tokens p50 | baseline 대비 ×1.5, n ≥ 8 | P3 | task |
| oracle `rust_worse` | covered cell에서 nightly 1건 이상 (uncovered cell은 제외) | P1 | bug |

- MARA는 T=0에서도 응답이 흔들립니다. 그래서 rate 신호는 n ≥ 8과 Wilson 하한으로만 내고, 단일 관측으로 내는 신호는 새 code와 운영 사건뿐입니다.
- 판정이 바뀐 live row는 `regressions/`에 복사합니다. 다음 PR에서 최소화해 fixture로 승격합니다. live에서 얻은 사례가 offline test로 들어오는 경로입니다.

**issue 생성 규칙** (AGENTS.md의 bd 규약):
- **dedupe key:** `llmconf:<signal>:<provider>/<model>:<task>:<code>`. label `llm-conformance`와 `--external-ref`로 넣습니다. 생성 전에 `bd list --label llm-conformance --status open --json`으로 같은 key를 찾고, 있으면 새로 만들지 않고 comment (최신 수치 + rec_id)를 추가합니다.
- **본문:** code, 수치 (n, rate, CI), 최초 관측 rec_id 3개, 재현 명령 `llmconf replay --rec <id>`, 관련 profile 경로**만** 넣습니다. `error.message`, `error_model_output`, `detail` 문자열, prompt나 응답 발췌는 넣지 않습니다 (bd는 remote로 push되고, 이 문자열은 private prompt에 대한 model reasoning을 echo합니다). issue renderer test가 본문의 field 집합이 위 목록뿐임을 assert합니다.
- **label:** `llm-conformance`, `model:<id>`, `task:<id>`, `signal:<kind>`.
- **상한:** run당 신규 issue 3개. 넘치면 요약 issue 1개로 묶습니다.
- **dry-run:** 처음 2주는 `--dry-run`이 기본이고 issue 초안만 `metrics/…/issues.jsonl`에 씁니다. 실제 `bd create`는 `--file-issues`를 명시해야 합니다. 오탐률을 보고 threshold를 조정한 뒤 켭니다 (Q8).
- **자동 close (후속, M5+):** 3회 연속 canary에서 신호가 사라지면 `bd close --reason "llmconf: signal cleared (runs …)"`. 첫 deliverable과 M3에서는 하지 않고 사람이 close합니다. dry-run 기간의 오탐률을 보기 전에 close를 자동화하면 신호가 흔들릴 때 issue가 열고 닫히기를 반복합니다.
- `bd` 호출은 trait로 분리해 test에서 실제 write를 하지 않습니다.

**scheduling:** T3/T4/T5는 systemd user timer (또는 paseo schedule)가 `llmconf catalog|canary|sweep`을 부릅니다. host는 MARA key가 있는 lab box입니다. `llm-conformance` issue의 triage는 agent가 맡을 수 있지만 agent는 profile PR까지만 만들고 `bless`는 사람이 승인합니다.

### 7.6 새 모델 / 형식 변화 onboarding (1시간 미만 runbook)

| 시간 | 단계 | 산출물 |
|---|---|---|
| 0-5 min | T3이 catalog diff로 issue 생성. 또는 canary가 `transport.unknown_wire_shape`/새 code로 issue 생성 (형식 변화이고 trigger recording이 이미 corpus에 있음) | bd issue + rec_id |
| 5-20 min | `llmconf onboard --model X` probe suite 약 25 call, ≤ $0.5. plain chat / `json_object` / `json_schema strict` 강제 판별 (enum 위반을 유도하는 schema를 보내 위반이 나오는지, 400이 나오는지) / forced tool_choice / tool auto / `enable_thinking` on/off / `reasoning_effort` / `max_tokens=64` (잘림 형태가 body `length`인지 HTTP 400인지) / 의도적 잘못된 param (error body 형태) | recording 약 25개 |
| 20-30 min | `llmconf profile-suggest --model X`: recording에서 reasoning field 위치, markup, 400 문구, usage key, supports flag를 추출해 profile 초안과 evidence rec_id 생성 | `profiles/mara/X.json` 초안 |
| 30-45 min | `with_overlay`로 초안 적용, canary case replay + live 1회. 남은 `unknown_wire_shape`가 있으면 그 recording을 fixture로 삼아 decoder rule 추가 (이 경우만 Rust 변경) | unknown 0건 |
| 45-60 min | `llmconf bless --model X` → PR (profile + committed 가능 recording + expectation). canary 대상에 자동 포함 | PR, issue close |

형식 변화가 기존 profile 범위 안이면 (예: 400 문구만 바뀜) 30분 안에 끝내는 것이 목표입니다.

### 7.7 oracle diff (migration 기간)

**oracle은 task carrier별로 둘입니다.** 기존 `_build_response` (room:store/llm.py:1115-1152)는 `message.tool_calls`를 읽지 않고, content가 비면 `reasoning_content` → `reasoning`으로 넘어갑니다. forced tool 응답 (content=null)에서 gpt-oss는 항상 `message.reasoning`을 같이 주므로, 이 경로는 reasoning prose를 답 text로 삼아 거기서 salvage합니다. 그래서 tool_call task에는 쓸 수 없습니다.

| oracle | 적용 대상 | 경로 | Python 결과 `P` |
|---|---|---|---|
| **content oracle** | content carrier task (`graph_extraction`, `query_slots`, `t2c_judge` 등 json 계열), HTTP 2xx, `tool_calls` 없음 | `_build_response` → `LLMResponse.json` (room:store/llm.py:210) → `extract_json_object` (room:llm_structured.py:132) | dict, 또는 fail (예외) |
| **tool-call oracle** | `tool_call(name)` task, HTTP 2xx | harness:finder acceptance의 `extract_message` 규칙: tool_calls가 정확히 1개이고 이름이 같으면 `json.loads(arguments)`. arguments가 이미 object이면 그대로 (harness:shared-knowledge agents) | dict, 또는 fail (None) |
| (없음) | `free_text`, non-2xx, content oracle 대상인데 `tool_calls`가 있는 응답, `carrier_intact` recording에서 치환된 field에 의존하는 판정 | — | `uncovered` |

- non-2xx와 retry 동작 (402/401 재전송 등)은 값 비교가 아니라 chain 비교 (§7.2 3번)로 봅니다.
- oracle.py는 `P`와 값만 냅니다. Rust diag code를 Python 쪽에서 흉내 내는 "code 근사치"는 만들지 않습니다.
- **coverage:** `(model, task)`별 covered 비율과 n을 metrics (`oracle_coverage`)와 baseline report에 냅니다. M2 gate는 covered recording에만 적용하고, covered n < 8인 cell은 보고만 하고 gate에서 뺍니다.

**결정 table.** 입력은 (1) `P` (값 `v` 또는 fail), (2) Rust verdict (`Ok|Salvaged` = 수용, 값 `r` / 그 외 = 거부), (3) `C(v)`: `v`를 Rust의 L→V→S에 넣었을 때 통과하는지 (같은 contract, 같은 policy), (4) `E`: lowering 후 canonical JSON 비교 `canon(L(v)) == canon(r)`. wire와 domain 형태 차이 (예: bare array, pairs)는 L을 양쪽에 적용하므로 차이로 세지 않습니다.

| # | `P` | Rust | `C(v)` | `E` | 분류 | divergence 목록 필요 |
|---|---|---|---|---|---|---|
| 1 | 값 | 수용 | — | 같음 | `agree` | 아니오 |
| 2 | 값 | 수용 | 통과 | 다름 | `value_diff` | **예** (예: 후보 선택 규칙). 목록에 없으면 gate 실패 |
| 3 | 값 | 수용 | 실패 | 다름 | `rust_better` (oracle 값이 contract 위반) | 아니오. 자동 사유 `oracle_value_violates_contract` + code별 집계 |
| 4 | 값 | 거부 | 통과 | — | `rust_worse`. 단 Rust의 결정 code가 목록의 **policy reject** 항목 (예: strict의 `extract.reasoning_carrier`, `transport.content_empty_reasoning_only`)이면 `rust_better` | 예외 처리에만 |
| 5 | 값 | 거부 | 실패 | — | `rust_better` (둘 다 계약상 실패, Python만 성공으로 보고) | 아니오. 자동 사유 + code별 집계 |
| 6 | fail | 수용 | — | — | `rust_better` | **예** (예: `<mm:think>`, harmony, bare array) |
| 7 | fail | 거부 | — | — | `agree` | 아니오 |
| 8 | 해당 oracle 없음 | — | — | — | `uncovered` | 아니오. gate 제외 |

- **M2 gate:** covered cell에서 `rust_worse` = 0, 목록에 없는 `value_diff`와 row 6 `rust_better` = 0. row 3, 5의 자동 `rust_better`는 code별 건수를 보고하고, 한 code가 covered n의 20%를 넘으면 contract 오류 가능성으로 보고 사람이 검토합니다 (contract가 틀리면 Rust가 무엇이든 "better"가 되기 때문).
- Python은 subprocess로 실행하며 T2 (nightly, parser PR CI job)에서만 돕니다. `cargo test`에는 넣지 않습니다.
- `oracle_divergences.json` (사유, survey 근거, Python 쪽 원인 file:line, 적용 row) 초기 목록:
  - `<mm:think>`, harmony, 닫히지 않은 think 처리 (row 6)
  - bare array 수용 (row 6)
  - "최대 object" 대신 "schema를 통과하는 최대 후보" 선택 (row 2)
  - greedy regex parser 3곳과의 차이 (row 2, 6)
  - reasoning carrier를 `Salvaged`로, strict에서는 `Repairable`로 표시 (row 4 policy reject)

## 8. 불변식

| # | 불변식 | 강제 지점 | 테스트 |
|---|---|---|---|
| C1 | core는 pure (I/O, clock, env, random 없음) | crate deps, `cargo tree` CI, 소스 grep | dep 검사. 같은 입력 1,000회 실행 시 fingerprint 동일 |
| C2 | contract compile은 결정적이고, `canon`은 Python `_stable_json`과 byte 단위로 같음 | `canon`, 입력 정렬 | (a) Python `_stable_json` 대비 byte golden (non-ASCII, astral, float, 중첩). (b) golden contract hash를 PyO3 binding으로 Python에서 재계산해 일치. Python 쪽 contract compiler는 없으므로 독립 구현 간 hash parity가 아니라 같은 Rust 구현의 언어 경계 parity입니다. hash 입력에 `crate_semver_major`가 있으므로 major bump 시 golden을 갱신 |
| C3 | Structural exposure의 wire schema와 OUTPUT_SCHEMA text는 ontology와 무관 | Structural compile 경로가 ontology를 읽지 않음 | ontology 3종 (FIBO, Entity 단일, FinDER)에서 bytes 동일 |
| C4 | `Validated<T>`는 V와 S를 통과한 값만 | pub ctor 없음 | `compile_fail` doctest |
| C5 | adapter 추가는 profile data만으로 | `ReasoningMarkup` 구현 목록 고정, profile lint | profile 추가 PR에 `.rs` diff가 있으면 CI 경고 |
| C6 | Terminal code는 재전송을 만들지 않음 | `next_step` | 402/401 fixture → `Stop`/`FallbackModel`만 |
| C7 | reasoning에서 캐낸 값은 `Ok`가 아님 | verdict | M2.7 fixture → `Salvaged` + `extract.reasoning_carrier` (strict: `Repairable`) |
| C8 | receipt의 contract hash = 실제 판정에 쓴 contract | `Validated.contract`, receipt 생성 | receipt hash가 `c.sha256()`과 같음 |
| C9 | 모든 dialect가 같은 wire schema에서 나옴 | dialect lowering 함수만 존재 | dialect별 schema를 역변환하면 L1과 같음 |
| C10 | `body_fidelity: raw` recording의 response는 원본 bytes | recorder (RRTransport raw 저장), importer는 `reserialized`만 생산 | `raw` recording의 `body_sha256` 재계산 일치. recorder에 재직렬화 경로 없음. byte 의존 fixture가 `reserialized`를 참조하면 CI 실패 |
| C11 | replay는 network를 쓰지 않고, 없는 recording은 실패 | ReplayMiss fail closed | lenient flag가 test build에서 거부됨 |
| C12 | committed tier에는 public/synthetic 입력만 | case manifest `input_class`, CI | private case를 committed에 넣으면 실패 |
| C13 | live 호출은 budget cap 안에서만 | BudgetGuard ledger | fake ledger로 cap 초과 시 0 request |
| C14 | `interpret`는 panic하지 않음 | lint (unwrap/expect deny), mutation test | seeded mutation 1만 회 |
| C15 | 논리 호출 1개의 HTTP attempt 수 = `next_step`이 계획한 수 | client `max_retries=0` 또는 transport 계층 `sdk_retry` attribution | recorder/driver test: fake transport가 429/5xx를 돌려줄 때 attempt 수가 계획과 같음 (SDK 숨은 재시도 0) |
| C16 | 여러 diag가 있을 때 next_step은 §6 우선순위의 최상위 class가 결정 | `next_step` | length+reasoning-only, length+tool-args chain fixture → `Retry{MaxTokens}` |

---

## 9. 마이그레이션 계획 (strangler-fig)

공통 제약: Python package 변경은 별도 branch에서 하고, 새 module 추가와 기존 파일의 최소 수정만 합니다.

| M | 내용 | 진입 | 종료 기준 | rollback |
|---|---|---|---|---|
| **M1** (첫 deliverable, lean) decode + raw 기록 + T1 replay + canary | (a) recorder: RRTransport가 원본 bytes 저장 (`body_fidelity: raw`), client `max_retries=0`, `seocho.llm_exchange.v1` writer, response header allowlist, secret scrub 시 `replayable:false`, run 단위 `rec_id`. (b) importer 3종 (`reserialized`, finder-v1은 `carrier_intact`/`none`) → private corpus. carrier별 oracle runner (content, tool-call)와 coverage가 붙은 baseline. (c) `seocho-llm-io` **T/E/P + J**: content와 tool_call carrier, markup splitter 2종, diag code (provider/transport/extract/parse), `next_step` 우선순위 table, profile `default` + `mara/_provider` + MARA 5모델. contract 없이 동작 (P의 후보 선택은 "최대 후보", contract가 있으면 M2에서 L1 기준으로 교체). (d) `llmconf replay` T1을 `cargo test`에. (e) `llmconf catalog` (hourly) + `canary` (4 contract, Python recorder subprocess), BudgetGuard, issue **dry-run** | 이 문서 승인 | T1 < 2 s. canary recording 100% `raw`. covered cell에서 oracle (§7.7 row 1, 2, 4, 6, 7. `C(v)` 열은 M2부터) `rust_worse` = 0. chain fixture (C15, C16) green. canary 1주 운영과 실측 비용 기록. private corpus MANIFEST와 모델별 replay class 비율 | 실험 코드와 crate 추가뿐. timer 중지 |
| **M2** contract + 판정 완성 | `seocho-contract`: `rules.rs` 분할 후 `canon` 이전, js1, 외부 tool schema normalize, lowering, L3 rule 일부, `compile` (`graph_extraction` Structural, `t2c_judge`, `query_slots`, `tool_call`). `interpret`에 L/V/S 추가, P 후보 선택을 L1 기준으로. 결정 table 전체 (`C(v)` 포함). profile 9종 (registry 1:1 변환), `plan_request`/`build_request`, `llmconf bless`/`diff-oracle`. 남은 task의 contract | M1 | covered cell에서 `rust_worse` = 0, 목록 밖 `value_diff`/row 6 = 0. C2 (canon byte parity + PyO3 golden hash), C3 green. 모든 TaskKind에 contract와 case ≥ 2. interpret p99 < 200 µs (release, ≤ 64 KiB) | crate 삭제 |
| **M3** live loop 확장 | `llmconf sweep/onboard/profile-suggest`, canary를 8 contract로. MARA `json_schema_strict` probe 결과 확정 | M2 | 2주 연속 canary 운영, 실측 월 비용 ≤ $60 (§7.4 표를 실측으로 갱신), dry-run issue 오탐 검토 완료 (Q8) | timer 중지 |
| **M4** PyO3 shadow | `seocho-core`에 `llm_io` 노출. room `complete_with_task_hints` (room:store/llm.py:363)와 `LLMResponse.json`에 `SEOCHO_LLM_IO=python\|shadow\|rust`. shadow는 양쪽을 실행하고 Python 결과를 쓰며, diff를 `seocho.gen_ai.llm_io.shadow_diff`로 내보냄. seocho-fix.11 attribute 채움. prod-shadow raw body sampling은 아래 진입 조건 충족 후에만 | M3, **Q6/Q7 보존·접근 정책 결정 (보존 기간, 암호화, 접근자, 삭제 절차)**, shadow 경로의 `max_retries=0` 또는 transport 계층 `sdk_retry` attribution | ERB/FinDER subset과 GraphCoT s1 run에서 미분류 diff 0. `shadow_skipped` = 0. prod-shadow recording의 chain 검사에서 설명 안 되는 attempt 0 | env 기본값 python |
| **M5** task별 authoritative + retry ladder 교체 | task 단위 `rust` 전환: `query_slots` → `extraction_critique` → `grounded_text2cypher` → `tools._safe_json` → `LLMResponse.json` (extraction, linking 포함). retry ladder (room:store/llm.py:696)를 `next_step` driver로 교체하고 같은 변경에서 client `max_retries=0` (H1 해소, 재시도 중복 방지). rust mode에서 native 부재는 `ImportError` | M4 soak 1 release | strict-mode FinDER에서 heuristic fallback 0 (ub5 교란 제거). 402/401 재전송 0 (receipt로 확인). C15 test green (논리 호출당 HTTP attempt = 계획 수). room에서 greedy `\{.*\}` 사용처 0 (test로 고정). 기존 Python test green | task별 env |
| **M5+** (후속, parse/repair loop가 한 task 이상에서 authoritative가 된 뒤 판단) | G3/§5.5 receipt chain 연결 (agent profile `output_contracts`), seochod `Node`/`Relationship` 공유 rlib 추출, bd issue 자동 close, M3b Rust live recorder (Q4), `Exposure::Vocabulary` (M6 실험 arm, Q1) | M5 | 항목별 별도 issue와 종료 기준 | 항목별 |
| **M6** constrained decoding | `plan_request`를 실제 적용: vLLM/openai extraction → `json_schema` strict (Structural). MARA → probe 결과와 실험 gate에 따라 ForcedTool. **실험 gate (사전 등록):** strict FinDER/GraphCoT에서 json_object 대비 failed_rate 감소, answer quality 비열등. Vocabulary exposure는 별도 arm, firewall ADR 이후 | M5, Q1 | gate 통과 모델만 profile 기본값 전환 | profile mode 순서 되돌림 |
| **M7** 은퇴 | Python `extract_json_object`, `_THINK_RE`, strip ladder, ad-hoc parser 삭제. shim만 유지. `oxigraph_read_model` `schema` op 검토 (Q3) | M5 이후 2 release | parse site의 `json.loads(...text...)` 0 (test로 고정) | wheel 되돌림 |

**bd 이슈 매핑**

| 이슈 | 관계 |
|---|---|
| seocho-ub5 (open, room) | M2 (C7 reasoning carrier, `<mm:think>`), M5 (silent fallback 제거), M6 (vLLM guided). "model-fallback retry"는 `NextStep::FallbackModel` |
| sr-z5g (open, rust) | `provider.payment_required` Terminal, `provider.tool_choice_unsatisfied` → Retry/FallbackModel. fixture는 live smoke의 preflight/coordinator recording. gpt-oss forced tool_choice@512 → `Retry{MaxTokens}` 계획을 canary로 검증 |
| seocho-fix.11 | `seocho.llm_interpretation.v1` receipt (verdict, reasoning_tokens)와 metrics로 충족 |
| seocho-c2a5, seocho-zpoj | provider code, backoff, canary metric. gemma의 "extra tool call"은 `provider.tool_choice_wrong_tool` 계열 + profile |
| sr-5ev, sr-mz6 | 같은 실패 유형 (조용한 native fallback). §4.7 no-fallback 방침 |
| 신규 등록 대상 | epic, M1 하위 issue (§11 제안 목록). 별도로: (1) `mara` default_model M2.7 stale (H6). (2) MaraBackend 120 s vs 900 s timeout. (3) `ROLE_FALLBACKS` M2.5 stale. (4) retry ladder 402/401 재전송과 SDK 기본 `max_retries` 중첩 (H1, M5 전 임시 수정 여부). (5) usage reasoning_tokens 누락 |

---

## 10. 테스트 전략, 리스크, 열린 질문

### 10.1 테스트 전략

1. **T0 unit (layer별):** markup splitter (think, mm:think, harmony, unterminated, 중첩, 문자열 안의 가짜 marker), balanced span scanner (escape, unicode), repair (trailing comma, 꼬리 닫기), js1 validator (keyword별 positive/negative), lowering, L3 rule (broader closure, Any wildcard, Entity 선언 여부 분기). 각 rule에 최소 bytes fixture를 둡니다.
2. **golden contract:** `contract/v1/contracts/*.json`. task × exposure × ontology 3종의 contract, hash, dialect별 wire schema. Rust에서 검증하고, PyO3 binding으로 Python에서 같은 hash가 나오는지 확인합니다 (C2, C3, C9). M2부터입니다.
3. **T1 replay:** expectation, self-consistency, chain (§7.2).
4. **oracle differential:** §7.7. M2에서 M5까지 parser PR CI job 필수입니다.
5. **seeded mutation:** proptest가 offline에 없으므로 seeded xorshift driver를 씁니다 (lifecycle 설계와 같은 방침). Ok recording의 content에 fence 삽입, think 4종 삽입, prose 앞뒤, 꼬리 절단, 문자열화, array 감싸기를 가합니다. 확인할 성질: (a) panic 없음, (b) 변형이 fence/prose/think뿐이면 `value_sha256`이 원본과 같고 diag가 변형을 지목함, (c) 절단이면 `Ok`가 아님.
6. **type 수준:** `compile_fail` doctest로 `Validated<T>` 외부 생성 불가 (C4).
7. **schema contract:** `projection` contract가 seochod test fixture payload를 accept하고 깨진 endpoint label을 reject합니다.
8. **budget과 issue:** fake ledger로 cap 초과 시 0 request. 402 recording replay 시 `Stop`. 기록된 metrics 시퀀스로 dedupe와 상한 (자동 close는 M5+). issue 본문 field 집합이 code, 수치, rec_id, 명령, profile 경로뿐인지 assert (bd는 trait 주입).
10. **retry attribution (C15):** fake transport가 429/5xx를 돌려줄 때 논리 호출당 HTTP attempt 수가 `next_step` 계획과 같음. client `max_retries` 기본값이 남아 있으면 실패.
11. **diag 우선순위 (C16):** §6 chain fixture (length+reasoning-only, length+tool-args).
9. **Python 쪽:** shadow hook unit test, native 부재 시 동작 (shadow는 skip + metric, rust mode는 `ImportError`), `next_step` driver가 402에서 재전송하지 않음.

### 10.2 리스크

1. **firewall 정책 위반 가능성.** constrained decoding은 Structural이어도 출력 분포를 바꿉니다 (예: 빈 결과 비율). M6는 사전 등록 실험 gate 뒤에만 열고 Vocabulary는 별도 arm으로 둡니다.
2. **dialect 차이.** OpenAI strict는 모든 property의 `required`와 open object 금지를 요구하고, vLLM과 forced tool은 더 관대합니다. `PairsToMap` lowering이 wire shape을 바꾸므로 prompt 예시와 domain 표현이 어긋날 수 있습니다. OUTPUT_SCHEMA를 contract에서 생성해 완화합니다.
3. **canonical JSON parity.** float repr이나 `default=str` 경로가 Python과 어긋나면 hash가 갈라집니다. 입력 snapshot의 비JSON 타입 금지와 golden test로 고정합니다.
4. **replay 대표성.** canary case 16개는 production 분포를 대표하지 못합니다. M4 prod-shadow sampling으로 실제 분포를 private corpus에 계속 넣습니다.
5. **기록 시점 판정이 틀린 경우.** self-consistency는 결정성만 보장하고 정답은 보장하지 않습니다. 사람의 bless 승인과 oracle diff가 방어선입니다.
6. **MARA drift.** catalog, error 문구, SambaNova body 형식이 바뀔 수 있습니다. `error_patterns`는 문자열 포함 검사라 취약합니다. 공통 문구는 `mara/_provider`에 두어 모델 profile 누락으로 분류가 빠지지 않게 하고, `provider.unclassified_4xx`는 run을 멈추지 않는 n=1 신호로, `transport.unknown_wire_shape`도 n=1 신호로 둬서 감지합니다.
7. **salvage가 지나치게 관대해질 위험.** repair를 늘릴수록 "잘못된 JSON을 그럴듯하게 고친" 결과가 늘어납니다. repair 종류를 고정하고, `Salvaged`를 별도 비율로 추적하고, strict에서 Terminal로 승격하는 option을 둡니다.
8. **비용 폭주.** 긴 입력과 repair chain이 예상보다 비싸면 목표를 넘습니다. 일/월 hard cap과 V3.2 cadence 축소로 대응합니다.
9. **private corpus 관리.** full prompt와 reasoning에 고객 문서가 들어갈 수 있습니다. 0700 권한, git 밖 보관, committed tier 입력 분류 CI 검사, response header allowlist, secret scan CI gate로 대응합니다. M4 prod-shadow sampling은 Q6/Q7 (보존 기간, 암호화, 접근자, 삭제 절차)이 결정되기 전에는 시작하지 않습니다 (M4 진입 조건). bd issue 본문에는 원문을 넣지 않습니다 (§7.5).
10. **PyO3 build 가용성.** native가 build되지 않는 환경에서 shadow가 조용히 비게 됩니다. `shadow_skipped`를 CI와 dashboard에서 0으로 assert합니다.
11. **diag code 폭증.** code가 잘게 나뉘면 issue가 시끄러워집니다. code 추가는 PR review 대상으로 둡니다.

### 10.3 열린 질문

1. **Q1.** `Vocabulary` exposure를 어디에서 허용할지 (extraction은 firewall ADR 필요. `query_intent`/`ontoclean`은 firewall 대상이 아니라는 해석이 맞는지).
2. **Q2.** `_output_contract_sha256`을 graph node에 stamp할지 (seochod `props_send` 변경, main.rs:189-217), agent profile을 통한 전이적 고정으로 충분한지.
3. **Q3.** `OntologySnapshot`의 장기 출처: Python exporter 유지, `oxigraph_read_model` `schema` op로 bundle sha에 직접 묶기, `infer_rules_from_nodes` 출력만 사용 중 무엇으로 할지.
4. **Q4.** M3b Rust live recorder를 할지. reqwest/tokio는 registry cache에만 있고 Cargo.lock에는 없습니다. workspace에 tokio/rustls 의존성이 들어오는 비용을 따져야 합니다.
5. **Q5.** corpus 압축이 필요한지. flate2/zstd가 offline에 없으므로 vendor 추가 여부를 정해야 합니다. 비압축 committed 20 MB 상한이 충분한지는 M2에서 확인합니다.
6. **Q6.** private corpus 보존 기간, 위치, 암호화. data: 쪽에는 이미 DB data가 15 GB 있습니다. **M4 진입 전 결정 필수.**
7. **Q7.** prod-shadow sampling 비율과 접근 권한 (비용 0, 저장량과 private 데이터 노출 범위 문제). **M4 진입 전 결정 필수.**
8. **Q8.** 자동 issue를 켜는 기준 (dry-run 종료 조건, 허용 오탐률).
9. **Q9.** Agents SDK 경로 (room:store/llm.py:1066, room:integrations/openai_agents.py:207)를 같은 core로 덮을지. SDK가 HTTP를 소유하므로 raw bytes 접근 방법 (httpx transport hook)이 필요합니다. 최소한 tool-args parser는 M5에서 공유합니다.
10. **Q10.** `contract_rev` bump 규칙: prompt template 변경도 contract rev를 올리는지, prompt는 PromptPackage hash로 따로 둘지.

---

## 11. 첫 번째 구현 단위 제안

M1은 첫 deliverable이고 범위를 일부러 좁혔습니다: **raw 기록, T/E/P decode, T1 replay, canary**. `seocho-contract`, L/V/S, contract hash parity는 M2입니다. G3/§5.5, Vocabulary, M3b, bd 자동 close는 M5+입니다 (§9).

**PR-1: RRTransport raw 저장 + `seocho.llm_exchange.v1` spec + importer + carrier별 oracle baseline.** Python package 변경과 Rust production 경로 변경은 없습니다.

범위:
- RRTransport (harness:shared-knowledge)가 `upstream.read()` 원본 bytes를 `body_b64`/`body_utf8`로 저장하고 replay 시 그대로 반환. client `max_retries=0` 확인 test. response header allowlist. secret scrub 시 `replayable:false`.
- `rust:crates/seocho-llm-conformance/spec/llm-exchange-v1.md`와 golden JSON fixture 8개: 200 content, 200 reasoning-only (content 빔), forced tool_call 문자열 args, forced tool_call object args, 400 tool_choice + `error_model_output`, 400 "did not complete", 400 JSON truncation, 402 body (live smoke preflight의 실제 402 body 형태를 synthetic으로 재구성). 구현된 spec은 fixture 13개와 stdlib Python reference validator/generator (`rust:crates/seocho-llm-conformance/python/`)를 포함합니다.
- importer (`import.py`, 내부 실험 code이며 이 저장소에는 없음): sk-llm/1 (`reserialized`), finder-v1 (`reserialized`, `carrier_intact`/`none`), room diagnostics를 v1로 변환해 `data:llm-corpus/`에 씁니다. 원본은 읽기만 합니다. 원본 경로와 sha256을 `source.origin`에 남깁니다.
- oracle runner (`oracle.py`, 내부 실험 code이며 이 저장소에는 없음): content oracle과 tool-call oracle (§7.7). 출력은 `P` (값 또는 fail)와 coverage뿐이고 code 근사치는 내지 않습니다.
- baseline report: model × task별 oracle 성공률, coverage, status 분포, reasoning_only 비율, token (completion+reasoning p50/p95), latency, replay class 비율.

종료 기준: importer test green, private corpus MANIFEST sha 기록, baseline report를 epic issue에 첨부.

**PR-2: `seocho-llm-io` T/E/P/J + profiles + `next_step` 우선순위 + T1 replay.**

범위:
- crate 생성 (C1 의존성 검사 포함). `Interpreter::embedded`, `interpret` (contract 선택적), `Outcome`, fingerprint.
- T: HTTP status 분류, `error_patterns` (`default` → `mara/_provider` → model 상속), usage 정규화.
- E: carrier 규칙 (tool_calls 우선, `extract.preamble_content`), markup splitter 2종 (harmony, think 계열), fence/prose, balanced span 후보.
- P: 제한된 repair, `finish=length` → `Repairable`. `ParseStringifiedJson` (문자열/object 둘 다).
- `next_step` 우선순위 table (§6)과 chain fixture.
- `llmconf replay`와 T1 `cargo test` (committed corpus: golden 8개 + synthetic + PR-3 이후 canary).

종료 기준: `cargo clippy -p seocho-llm-io --all-targets --offline -- -D warnings && cargo test -p seocho-llm-io -p seocho-llm-conformance --offline` green, T1 < 2 s, covered cell `rust_worse` = 0 (§7.7 M1 범위).

**PR-3: `llmconf catalog` + `canary` (Python recorder subprocess) + BudgetGuard + issue dry-run.**

범위: catalog probe (hourly), canary 4 contract × (짧은, 긴 입력), cadence §7.4, BudgetGuard ledger, metrics `seocho.llm_metrics.v1`, issue 초안 `issues.jsonl` (본문 field 제한), systemd user timer unit.

종료 기준: canary 1주 운영, recording 100% `raw`, 실측 비용을 §7.4 표와 비교해 epic issue에 기록.

**제안 M1 issue 목록** (epic 1개 + 하위 issue, `bd create` 시 `--deps parent:<epic>`)

| # | title | type | priority | 설명 |
|---|---|---|---|---|
| 0 | LLM I/O contract + MARA conformance harness (epic) | epic | 1 | 이 문서. M1 lean deliverable 추적 |
| 1 | RRTransport: store raw response bytes and pin max_retries=0 | task | 1 | 원본 bytes를 `body_b64/utf8`로 저장·replay, header allowlist, secret scrub → `replayable:false`, SDK 숨은 재시도 0 test |
| 2 | seocho.llm_exchange.v1 spec + golden fixtures | task | 1 | `body_fidelity`, `replay_class`, run 단위 `rec_id`/replay key, golden 8개 |
| 3 | Importer for sk-llm/1, finder-v1, room diagnostics into private corpus | task | 2 | `reserialized` 표시, finder-v1 `carrier_intact`/`none` 판정, 관측/body 분리 저장 |
| 4 | Carrier-split Python oracle (content + tool-call) and coverage baseline | task | 1 | `_build_response` 경로는 content task만, tool task는 `extract_message` 규칙. (model, task)별 coverage와 baseline report |
| 5 | seocho-llm-io crate: T/E/P decode, verdict, fingerprint | feature | 1 | content/tool_call carrier, markup 2종, 제한 repair, C1/C14 |
| 6 | Model profiles: default, mara/_provider (inherited error_patterns), MARA 5 models | task | 2 | "did not complete" 포함, unclassified 4xx 비terminal, evidence lint |
| 7 | next_step total order + chain fixtures | task | 1 | §6 우선순위 class, length+reasoning-only / length+tool-args fixture (C16) |
| 8 | llmconf replay + T1 committed replay in cargo test | task | 1 | ReplayMiss fail closed, run-scoped lookup, < 2 s |
| 9 | llmconf catalog + canary with BudgetGuard and issue dry-run | feature | 2 | 4 contract, 짧은 입력 4회/일·긴 입력 1회/일, 비용 실측, issue 본문 원문 금지 |
| 10 | Fix stale MARA defaults (M2.7 default, M2.5 fallback, 120 s timeout) | bug | 2 | H6. llm-io와 독립, Python package 쪽에서 |
| 11 | Retry ladder resends 401/402 and stacks on SDK max_retries | bug | 1 | H1. M5 전 임시 수정 (Terminal status 즉시 중단, `max_retries=0`) 여부 결정 |

M2 이후 항목 (`seocho-contract`, L/V/S, PyO3, receipt chain 등)은 M1 종료 시 별도 issue로 등록합니다.

---

## 12. 개정 이력 (review r1)

adversarial review r1 (verdict `needs_changes`)의 지적과 반영 내용입니다. 모든 지적은 코드로 확인했고 기각한 항목은 없습니다.

| 심각도 | 지적 | 반영 |
|---|---|---|
| blocker | Python oracle이 tool_calls를 처리하지 못함 (`_build_response`가 `message.tool_calls`를 읽지 않음, gpt-oss는 reasoning으로 fallback) | §7.7: oracle을 carrier별로 분리 (content oracle / tool-call oracle = finder `extract_message`, mara agents.py 규칙), `uncovered` 분류와 (model, task) coverage, gate는 covered cell만. H2에 tool_calls 무시 사실 추가. G9 수정 |
| major | `agree/rust_better/rust_worse/value_diff` 정의 없음 | §7.7 결정 table (P 결과 × Rust verdict × `C(v)` × lowering 후 값 동일성), M2 gate 재정의, oracle.py의 "code 근사치" 삭제 |
| major | 기존 corpus에 raw bytes 없음, C10 성립 불가 | §1.4 사실 정정, §7.1 `body_fidelity: raw\|reserialized`, raw만 C10·byte fixture 근거, RRTransport raw 저장을 M1 PR-1 첫 작업으로. C10 수정 |
| major | finder-v1 redaction 모순 (`clean()`이 reasoning, reasoning_tokens, error_model_output 치환) | §7.1 `replay_class: full\|carrier_intact\|none` 도입, finder-v1은 `carrier_intact` (문자열 치환 row는 `none`), finder-v1에 reasoning_tokens/error_model_output 없음 명시 |
| major | 여러 Retryable diag 충돌 시 next_step 순서 없음 | §6 우선순위 전순서 (Terminal > 전송 재시도 > budget/길이 > provider mode 거부 > reprompt > 비재전송), chain fixture 2종, C16 |
| major | H1이 OpenAI SDK 숨은 재시도를 누락 | H1 정정 (`max_retries` 미설정 → SDK 기본 2회, 논리 호출당 최대 약 12 HTTP), recorder/M4/M5에 `max_retries=0` 또는 transport attribution 필수, C15와 test |
| major | replay identity 모호, T=0 반복 관측 손실 | `rec_id`에 run_id·logical_call_id 포함, replay lookup을 run 단위로, body는 content-addressed dedupe·관측은 dedupe 안 함 |
| major | error_patterns가 모델별이라 좁음, unclassified 4xx Terminal, "did not complete" 누락 | `mara/_provider` 상속 profile, "did not complete" pattern, `provider.unclassified_4xx`를 비terminal (호출 단위 FallbackModel/실패 + n=1 신호)로 |
| minor | tool_calls와 같이 온 content 처리 미정 | §4.4 E: tool_calls가 있으면 tool_choice와 무관하게 carrier, content는 `extract.preamble_content`. M3 관측 근거가 없음을 §1.1(b)에 명시하고 canary fixture 확보를 M1에 |
| minor | tool args 두 인코딩 수용 미정 | `ParseStringifiedJson`이 object 통과 + `lower.args_object`, `tool_args_encoding`은 evidence 필요한 관측 사실, harness 재직렬화 blob은 `source.wire:false`로 profile-suggest에서 제외 |
| minor | js1이 기존 tool schema (`assign_work`)를 거부 | §5.2 L1: 외부 tool schema normalize + `contract.tool_schema_normalized` diag. agents.py 인용을 :28로 정정 |
| minor | file:line 오류, 관측/예상 혼재 | llm_structured.py:239, `{"vllm"}` (:83), ModelRouter routing/model_router.py:85, complete_with_task_hints :363, `LLMResponse.json` :210 등 정정. §1.1을 (a) 관측 / (b) 예상으로 분리, 402는 live smoke preflight recording 직접 인용 |
| minor | refactor 과소 서술 (rules.rs pyo3, seochod private type, C2) | §1.5와 §4.2에 module 분할 명시, seochod type 추출은 M5+로, C2를 canon byte parity + PyO3 golden hash로 재정의 |
| minor | 기록 보안 공백 | response header allowlist, secret scrub → `replayable:false` + CI scan gate, Q6/Q7을 M4 진입 조건으로, issue 본문은 code·수치·rec_id만 (renderer test) |
| minor | 비용 모델 낙관적 (completion 800 가정) | §7.4: finder-v1/GraphCoT 실측 completion(+reasoning) p50/p95 표, 모든 호출 p95(1,800) 가정, 긴 입력 하루 1회로 cadence 조정, 월 약 $47 상한 |
| minor | 범위 과다 | 첫 deliverable을 T/E/P + raw 기록 + T1 + canary로 축소. G3/§5.5, Vocabulary, M3b, bd 자동 close, seochod type 추출을 M5+로. §3.2, §9, §11 issue 목록 갱신 |
