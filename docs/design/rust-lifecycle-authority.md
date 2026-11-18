# 방향 2 설계안: Rust lifecycle authority (`seocho-lifecycled`)

- 상태: 제안 (draft)
- 대상: Rust workspace (`rust/`, 신규 crate), Python package (`src/seocho`, client facade)
- 결정 기록: [ADR-0239](../decisions/ADR-0239-rust-lifecycle-authority.md)
- 경로 약어 (모두 이 저장소 기준, 작성 시점의 revision):
  - `rust:` = Rust workspace (`rust/`). 예: `rust:crates/seochod/src/main.rs` = `rust/crates/seochod/src/main.rs`. 접두어 없는 `main.rs:N`은 이 파일입니다.
  - `room:` = #783 병합 이전의 Python 소스 (`src/...`, v0 store)
  - `xsu:` = #783 branch `fix/monotonic-lease-fencing-token`의 Python 소스 (v1 store, 이후 `main`에 병합)
- 행 번호와 `Cargo.lock:N` 참조는 작성 시점 기준이며 이후 변경으로 어긋날 수 있습니다.
- `sr-*` ID는 Rust workspace 쪽 issue tracker의 ticket입니다 (ADR-0239 참조): sr-xsu = fencing token 재사용 결함 (#783으로 수정), sr-x9x = 그 수정의 `main` 병합, sr-5e6 = mixed old/new minter의 token 중복, sr-5zg = Rust workspace의 공개 저장소 upstream, sr-sxl = daemon/socket 운영 문서, sr-5ev·sr-mz6 = native가 없을 때 조용히 통과하는 fallback/parity test, sr-e3z = 구 `dataplane/seochod` 버그, sr-tmz.1 = `audit` 판정 기준.

---

## 1. 배경과 문제

### 1.1 sr-xsu 사고

`main`의 `acquire()`는 token을 남아 있는 lease row에서 계산합니다: `fence = max(active.fencing_token + 1, MAX(ontology_lease.fencing_token) + 1)` (room:src/seocho/ontology/lifecycle.py:223). 그런데 `release()`는 row를 DELETE합니다 (room:lifecycle.py:242-245). 따라서 release된 token이 다시 발급될 수 있습니다.

seochod는 이 token을 DozerDB에 `_fencing_token`/`_fence_scope`로 stamp합니다 (rust:crates/seochod/src/main.rs:461-468). 그리고 `_fencing_token <= $fence` 조건으로 같은 package scope 안의 writer 순서를 결정합니다 (main.rs:404-406). token이 재사용되면 stale writer가 fence를 통과하거나 정상 writer가 fence에 막힙니다.

Rust 쪽은 이 전제를 주석으로만 갖고 있습니다: "the store issues these strictly increasing and never reused per (workspace, package)" (main.rs:196-200). 타입 시스템도 테스트도 이 전제를 볼 수 없습니다.

sr-xsu 수정 (xsu:src/seocho/ontology/lifecycle.py, 미병합)은 다음을 추가합니다.
- durable `ontology_fence_high_water` 테이블 (xsu:lifecycle.py:223-226)
- `_next_fencing_token`: `max(hw, live max, pointer) + 1`을 high-water에 UPSERT한 **뒤에** lease INSERT (xsu:lifecycle.py:299-315, INSERT는 :365)
- 같은 write txn 안에서 pointer 읽기 (xsu:lifecycle.py:350-356)

그러나 upgrade 순서는 docstring 규칙으로만 남아 있습니다: 모든 Python opener를 먼저 업그레이드하고, old/new `acquire()`를 동시에 돌리지 말 것 (xsu:lifecycle.py:21-37). sr-5e6은 mixed old/new acquire가 token을 중복 발급할 수 있음을 기록합니다.

### 1.2 현재 소유권 분할

| 대상 | 생성 | 쓰기 | 읽기/검증 |
|---|---|---|---|
| `active_ontology`, `generation_hwm` | Python (room:active_pointer.py:67-81) | Python `publish`/`recreate` (:103-191) | Python, seochod read-only (main.rs:255-259) |
| `ontology_lease` | Python (room:lifecycle.py:171-178) | Python acquire/renew/release | Python `admission()` (liveness만, :247-257), seochod (main.rs:231-254) |
| `ontology_fence_high_water` | Python, xsu만 (xsu:lifecycle.py:223) | Python acquire | Python만 |
| `projection_idempotency` | Python (room:lifecycle.py:179-183) | **Rust만** (main.rs:288-358) | Rust |
| `retired_ontology` | Python, 별도 파일 (room:reclamation.py:81-88) | Python | 미연결 |

이 분할에서 나오는 구체적 결함은 다음과 같습니다.

- **activation token을 caller가 고릅니다.** CLI `--fencing-token` (room:cli/ontology.py:218), calibration script의 `ptr + 1` (room:scripts/benchmarks/run_governed_memory_calibration.py:64)이 그 예입니다. `publish`는 같은 token도 허용합니다 (`int(fencing_token) < cur_tok`, room:active_pointer.py:153). 따라서 누구든 큰 token으로 publish해서 모든 lease를 fence할 수 있습니다.
- **`renew`에 txn, TTL 검증, purpose 검사가 없습니다** (room:lifecycle.py:232-240).
- **Python `admission()`은 purpose와 stale pointer를 보지 않습니다** (room:lifecycle.py:247-257, sr-5e6). 이 검사는 seochod만 합니다 (main.rs:241, :261-278).
- **clock이 둘입니다.** expiry는 Python `time.time()`으로 기록되고 (room:lifecycle.py:212,224), Rust의 `now_ms`로 비교됩니다 (main.rs:244).
- **Rust test fixture가 Python DDL을 손으로 복제합니다** (main.rs:1106-1114, 1233-1247).
- **daemon 구현이 두 개입니다.** 구 `dataplane/seochod/src/main.rs`(600줄, 이후 `rust/crates/seochod`로 대체되어 삭제)는 purpose 검사와 fence 이전 버전이고, sr-e3z 버그도 남아 있습니다 (:278).
- **pipeline key 오류:** room:src/seocho/index/pipeline.py:889는 `admission.get("fence")`를 읽지만 실제 key는 `fencing_token`입니다 (room:lifecycle.py:257). 그래서 `admission_fence`가 항상 `None`입니다.

### 1.3 거버넌스 문맥

- **방향 2를 지지하는 문서:** SEMANTIC_CONTROL_PLANE.md:302-306은 "names describe authority, not process placement"라고 씁니다.
- **방향 2와 충돌하는 문서:** ADR-0163:20, :60-61은 "control plane stays in Python"이라고 씁니다.
- **어떤 설계든 지켜야 할 제약:** "data-plane workers may not decide what is active" (SEMANTIC_CONTROL_PLANE.md:304-306, :359-361). 따라서 authority는 DozerDB writer(seochod)와 다른 privilege domain에 있어야 합니다.

---

## 2. 목표 / 비목표

### 목표

- **G1.** `(workspace_id, package_id)` scope별 token monotonicity와 never-reuse를 Rust 한 곳에서 보장합니다. 세 겹으로 강제합니다: type (mint만 생성 가능), DB trigger (어떤 언어의 writer든 적용), test (model, multi-process, legacy-writer replay).
- **G2.** lease state machine, pointer CAS, generation high-water, idempotency state machine, control-DB schema를 단일 owner(`seocho-lifecycle` crate)로 옮깁니다.
- **G3.** authority와 DozerDB writer를 서로 다른 process로 분리합니다. seochod는 verify-only로 남깁니다.
- **G4.** Python caller의 public API와 admission dict shape `{lease_id, fingerprint, generation, epoch, fencing_token}`을 유지합니다 (room:lifecycle.py:257, room:plane_policy.py:112-115).
- **G5.** sr-xsu high-water를 손실 없이 승계하고, DozerDB에 이미 stamp된 최대 token 위에서 시작합니다.
- **G6.** 각 단계를 rollback할 수 있게 합니다 (v2에서 v1 downgrade).

### 비목표

- multi-host. SQLite/WAL은 single-host 전용입니다 (SEMANTIC_CONTROL_PLANE.md:368-372). backend trait만 예약합니다.
- Postgres agent-memory fencing 통합 (room:postgres_sequence_v2.py:194-209). scope도 transaction도 다르므로 contract test만 공유합니다.
- policy 결정의 이전. promotion, governed/lockdown mode (room:plane_policy.py:58-116), purpose policy, bundle 검증과 hashing (room:lifecycle.py:43-71)은 Python에 남습니다.
- EBR pin (room:version_pin.py:57-75)의 이전. pin은 in-process memory로 남습니다.
- seochod의 performance 변경, Ed25519 signed capability (P5로 연기, §3 참조).

---

## 3. 검토한 대안

| | 요약 | safety-correctness | migration-effort | 판정 |
|---|---|---|---|---|
| **A** seochod-owns | seochod가 control DB의 유일한 writer. 같은 binary 안에서 actor thread가 mint/CAS 수행. authority-gate trigger(SQL function 등록 여부)로 외부 writer 차단 | 6.0 | **7.0** (winner) | 기각. Bolt password와 mint authority가 한 process/uid에 모여 SEMANTIC_CONTROL_PLANE.md:304-306과 :359-361을 정면으로 위반함. ADR waiver가 거절되면 중간에 B로 재구조화해야 함. serial daemon (main.rs:866-867, 981-983)을 3-thread로 바꾸는 회귀 위험도 있음 |
| **B** lifecycled | 별도 Rust daemon `seocho-lifecycled`가 유일한 writer이자 minter. seochod와 Python은 client | **7.5** (winner) | 5.5 | **채택 (base)** |
| **C** shared-core | Rust lib를 PyO3로 Python 각 process에 in-process 로드. seochod는 `verifier` feature만 link | 4.5 | 6.0 | 기각. (1) T3 `NEW.fencing_token <= hw` abort는 xsu mint 순서(high-water UPSERT 후 INSERT, xsu:lifecycle.py:309-313, :365)와 충돌해 모든 xsu acquire가 abort되므로, rollback 및 compat 주장이 거짓임. (2) 모든 Python process가 full authority를 가짐. (3) system sqlite 3.37.2와 bundled 3.46.0이 한 process에 공존하면 POSIX lock 소실로 DB가 손상될 수 있음 |

### 선택 근거와 trade-off

두 lens의 합산 점수는 A와 B가 13.0으로 같습니다. B를 base로 고른 이유는 세 가지입니다.

1. 방향 2의 동기는 correctness(sr-xsu)입니다. safety lens의 판정이 더 무겁습니다.
2. A의 최대 결함인 authority와 writer의 동거는 graft로 고칠 수 없는 구조적 결함입니다.
3. B의 결함은 대부분 graft로 줄일 수 있습니다.

| B의 약점 (migration lens) | 이 설계의 대응 |
|---|---|
| Ed25519 key 관리, 운영 부담 | 서명을 제거하고 P5(multi-host)로 연기합니다. single host에서는 precommit `check` RPC가 이미 authoritative합니다. capability는 opaque lease 참조로 다룹니다 |
| 0600 socket 하나에 세 user가 접속하는 모순 (factual error) | op 집합별로 socket 3개를 둡니다 (§4.3) |
| `mint = hw + 1` wedge: legacy UPDATE publish가 큰 token을 넣으면 high-water가 pointer 아래에 남음 | mint를 `max(hw, live, pointer) + 1`(A, xsu 규칙)로 바꿉니다. active_ontology UPDATE도 "mint된 token만" 허용하도록 T5를 강화합니다 |
| per-op caller authorization 부재 | socket별 dispatch table을 둡니다 (A graft) |
| DozerDB floor 누락 위험 | `migrate`는 floor 값이나 명시적 `--no-dozerdb-floor-i-attest` 없이는 거부합니다. floor 확인은 `migrate` 단계에서만 하고 `serve` 시작에는 걸지 않으므로, DozerDB 부재가 daemon 기동을 막지 않습니다 |
| 노력 4-5 engineer-week | 첫 PR은 library와 `audit`만 포함하고 production 변경이 없습니다 (§9) |

다른 graft와 그 출처는 다음과 같습니다.
- A: `Admitted` type을 `WriteScope::governed`의 유일한 입력으로, `request_nonce`/`client_op_id`
- C: `ddl_sha256`, `cargo tree` CI 검사, 명시적 `synchronous=FULL`, differential scenario, `min_token` assert path

기각한 graft도 있습니다. C의 "facade가 `now_ms` 전달"은 authority의 clock을 caller가 고르게 만들므로 lifecycled backend에는 적용하지 않습니다. test clock은 Rust `Clock` trait 주입으로 대신합니다.

### judge가 지적한 사실 오류 정정

- `sha2`: Cargo.lock:1273-1274는 **0.11.1**입니다 (0.10이 아님). 이 설계는 sha2를 쓰지 않습니다. bundle hashing은 Python에 남습니다.
- `proptest`와 `loom`은 offline registry에 **없습니다**. 따라서 seeded xorshift driver를 확정 방침으로 씁니다 (조건부 아님).
- C의 "`synchronous=FULL`은 WAL 기본값 NORMAL 대비 hardening" 주장은 틀렸습니다. system Python sqlite 3.37.2의 WAL 기본값은 이미 FULL(2)입니다. 이 설계는 build default에 의존하지 않으려고 명시적으로 설정할 뿐입니다.
- C의 "`_fence_scope`가 package_id뿐이라 workspace 간 counter가 비교된다"는 주장도 틀렸습니다. node MERGE는 `{id, _workspace_id}`로 key되고, relationship endpoint는 `_workspace_id`로 MATCH됩니다 (main.rs:420, :430). 다른 package scope는 `fence_guard`가 비교를 건너뜁니다 (main.rs:405). 따라서 token은 항상 같은 `(ws, pkg)` counter끼리만 비교됩니다. I10에서 이를 test로 고정합니다.
- A가 lockfile에 있다고 본 crate 중 실제로 확인된 것: `getrandom 0.2.17` (Cargo.lock:313-314), `hex 0.4.3` (:377-378), `ring 0.17.14` (:1049-1050), `rusqlite 0.32.1` (:1063-1064).

---

## 4. 제안 설계

### 4.1 구성도

```
 Python (seocho run / CLI / scripts)                      Rust
 ┌───────────────────────────────┐
 │ OntologyLifecycleStore facade │  operator.sock (0660, grp seocho-op)
 │  (signature unchanged)        │──────────────┐  activate/recreate/prune + client ops
 │ LifecycleClient, LeaseKeeper  │  client.sock  │
 │ ro SQLite reader (pins/status)│──────────────┤  acquire/renew/release/admit/read_admit/status/hello
 └──────────────┬────────────────┘               v
                │ file:...?mode=ro     ┌──────────────────────────────────┐
                │                      │ seocho-lifecycled                │
                │                      │  conn threads (bounded) ──mpsc──>│
                │                      │  Owner thread: sole RW conn      │
                │                      │   Authority<SqliteBackend,Clock> │
                │                      │   Minter (only token source)     │
                │                      │  flock(<db>.lifecycled.lock)     │
                │                      └──────────────┬───────────────────┘
                │                 writer.sock (0660, grp seochod) ^  check/idem_claim/idem_complete/hello
                v                                     │ sole writer      │
   ┌──────────────────────────────────────┐           v                  │
   │ state.sqlite (WAL, synchronous=FULL) │<──────────┘     ┌────────────┴──────────┐
   │ schema v2 + triggers T1-T8           │                 │ seochod (Bolt pw only) │
   │ lifecycle_meta{db_uuid,ddl_sha256}   │                 │ links seocho-lifecycle-│
   └──────────────────────────────────────┘                 │ proto only; no mint    │
                                                            └────────────┬───────────┘
                                                                         v DozerDB
```

### 4.2 crate와 process

| crate | 종류 | 의존성 (모두 offline, lockfile 존재) | 역할 |
|---|---|---|---|
| `crates/seocho-lifecycle-proto` | rlib | serde, serde_json | wire types, stable `ErrorCode`, `ObservedToken`, `Capability`, blocking `LifecycleClient`. **seochod는 이 crate만 의존** |
| `crates/seocho-lifecycle` | rlib | rusqlite 0.32.1 (bundled), getrandom 0.2.17, hex 0.4.3, proto | domain types, `Authority<B, C>`, `LifecycleBackend` trait, `SqliteBackend`, `schema` (유일한 DDL), `schema::create_for_test` |
| `crates/seocho-lifecycled` | bin | 위 둘, std만 (tokio 없음) | subcommand `serve`, `migrate [--dry-run]`, `audit`, `downgrade --to-schema 1` |

- 이전 lockfile 근거: getrandom 0.2.17 (Cargo.lock:313), hex 0.4.3 (:377), rusqlite 0.32.1 (:1063).
- workspace lint(`unsafe_code = forbid`, clippy deny `unwrap_used`/`expect_used`)를 그대로 따릅니다 (rust:Cargo.toml).
- 동시성 모델: connection당 thread(상한 있음)와 단일 Owner thread입니다. 모든 write와 linearizable read는 Owner가 직렬화합니다. 각 connection에 2 s read deadline을 두어 seochod의 serial loop (main.rs:981-983)와 달리 느린 client 하나가 다른 client를 막지 못합니다.
- single writer: `std::fs::File::try_lock` (Rust 1.89에서 stable, toolchain 1.94.1)으로 `<db>.lifecycled.lock`을 process 수명 동안 잡습니다.
- PyO3는 쓰지 않습니다. 이유: (a) privilege boundary가 없습니다. (b) 두 SQLite copy hazard가 생깁니다 (C의 판정). (c) fallback 패턴 (rust:crates/seocho-core/python/seocho_core/__init__.py:7-22)은 authority에 부적합합니다. sr-5ev와 sr-mz6가 "native가 안 돌아도 조용히 통과"하는 같은 실패 유형을 이미 보여줍니다.

### 4.3 API (`seocho.lifecycle.v1`)

**framing:** newline-JSON, 연결당 1 request, 요청과 응답 각 64 KiB 상한, 2 s deadline. seochod의 framing (main.rs:24, 868-908)과 stale-socket 처리 (main.rs:942-968)를 재사용합니다.
- 요청: `{"v":1,"op":..,"request_id":..,"client_op_id"?:..}`
- 응답: `{"ok":bool,"code"?:ErrorCode,"error"?:str,...}`
- `ErrorCode`: `lease_absent`, `lease_expired`, `lease_held`, `owner_mismatch`, `not_projection_purpose`, `stale_pointer`, `fenced`, `cas_mismatch`, `ttl_out_of_range`, `schema_mismatch`, `schema_tampered`, `busy`, `internal`.

**socket별 dispatch.** 다른 socket에서 온 op는 `code=invalid_request`로 거부합니다.

| op | operator | client | writer | 비고 |
|---|:-:|:-:|:-:|---|
| `hello` | ✓ | ✓ | ✓ | `{protocol, schema_version, db_uuid, ddl_sha256, instance_id}` |
| `status{ws,pkg}` | ✓ | ✓ | | 한 read txn의 일관된 snapshot (현재는 두 connection, room:lifecycle.py:259-264) |
| `acquire{ws,pkg,purpose,owner,ttl_ms,client_op_id}` | ✓ | ✓ | | 같은 `client_op_id`면 같은 grant 반환 |
| `renew{lease_id,owner,ttl_ms}` | ✓ | ✓ | | write txn, `0 < ttl_ms <= max_ttl_ms`, pointer 변경 시 `stale_pointer` |
| `release{lease_id,owner}` | ✓ | ✓ | | row를 tombstone으로 이동 |
| `admit{lease_id,ws}` | ✓ | ✓ | | 5-field dict + `purpose`, `expires_at_ms`. purpose ≠ projection이면 거부 (sr-5e6) |
| `read_admit{ws,pkg,fingerprint}` | ✓ | ✓ | | lease row 없음, token 소비 없음. `query` lease 직렬화 (room:online_query_admission.py:37-70) 대체 |
| `activate{ws,pkg,version,fingerprint,expected,min_token?,client_op_id}` | ✓ | | | token은 **mint**. `min_token`은 legacy `--fencing-token`을 assert로만 해석 (mint된 값이 더 작으면 `fenced`) |
| `recreate{..., expected}` | ✓ | | | CAS 추가 (현재 CAS 없음, room:active_pointer.py:170-191) |
| `prune{older_than_ms}` | ✓ | | | tombstone retention만 정리. high-water는 건드리지 않음 |
| `check{capability}` | | | ✓ | precommit 재검증. main.rs:218-279 규칙을 그대로 이식 |
| `idem_claim{key,ws,fingerprint,capability}` | | | ✓ | admission과 claim을 **한 txn**에서 처리 (현재는 별도 open 2회, main.rs:733-770) |
| `idem_complete{key,claimed_at_ms,response_json}` | | | ✓ | `created_at_ms` CAS 유지 (main.rs:352-355) |

**Rust 핵심 시그니처** (`seocho-lifecycle`):

```rust
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct FencingToken(NonZeroU64);            // no pub ctor, no Deserialize
pub(crate) struct WriteTxn<'c>(rusqlite::Transaction<'c>); // only via BEGIN IMMEDIATE
impl WriteTxn<'_> {
    // token = max(hw, MAX(live lease), pointer) + 1; UPSERT hw=token, minted_for=?; the ONLY token source
    pub(crate) fn mint(&mut self, scope: &Scope, minted_for: MintFor) -> Result<FencingToken, StoreError>;
}
pub trait LifecycleBackend { type Txn<'a>; fn write(&mut self) -> Result<Self::Txn<'_>, StoreError>; fn read(&self) -> Result<ReadSnapshot<'_>, StoreError>; }
pub trait Clock { fn now_ms(&self) -> Millis; } // persisted non-decreasing wall clock
```

seochod 쪽 (`seocho-lifecycle-proto`만 사용):
- `ObservedToken(u64)`는 비교만 가능하고 `FencingToken`으로 변환할 수 없습니다.
- `validate_admission` (main.rs:211-280)은 `trait LifecycleVerifier { admit, recheck, claim, complete }`로 바뀌고 구현이 두 개입니다.
  - `SqliteReadVerifier`: 현행 코드, P1-P2와 compat shim.
  - `LifecycledVerifier`: writer.sock RPC, P3부터.
- verifier만 만들 수 있는 `Admitted` 값이 `WriteScope::governed(&Admitted)`의 유일한 입력이 됩니다. 현재 free tuple인 `Fence { token, scope }` (main.rs:448-452)를 대체합니다.

**Python** (room, 신규 `src/seocho/ontology/lifecycle_client.py`):
- `LifecycleClient`와 `LeaseKeeper`를 둡니다. `LeaseKeeper`는 ttl/3마다 renew하고, 실패하면 `.lost`를 표시해 다음 batch를 fail closed로 만듭니다.
- `OntologyLifecycleStore`는 `SEOCHO_LIFECYCLED_SOCKET`이 설정되면 mutating method를 client에 위임합니다. 반환 shape은 바꾸지 않습니다.

### 4.4 Control-DB 소유와 schema versioning

- **owner:** `seocho_lifecycle::schema`가 유일한 DDL source입니다. socket이 설정되면 Python facade는 `_init_schema`를 건너뛰고 `lifecycle_meta`만 검사합니다.
- **versioning:**
  - `PRAGMA user_version`과 `lifecycle_meta(key PK, value)`를 둡니다. 내용: `schema_version`, `db_uuid`, `writer='seocho-lifecycled'`, `ddl_sha256` (owned object의 정규화된 `sqlite_master.sql` hash), `migrated_from`, `last_wall_ms`.
  - v0은 Python main, v1은 xsu (`ontology_fence_high_water` 존재), v2는 lifecycled입니다.
  - lifecycled는 정확히 v2이고 `ddl_sha256`이 일치할 때만 serve합니다. 불일치면 `schema_tampered`로 거부합니다.
- **v2 변경** (seochod P2가 읽는 column의 의미는 보존):
  - `ontology_fence_high_water`에 `minted_for TEXT`와 `CHECK(high_water >= 0)`을 추가합니다.
  - `ontology_lease`에 `UNIQUE(ws, pkg, purpose)`와 `UNIQUE(ws, pkg, fencing_token)`을 추가합니다. acquire는 같은 txn에서 만료 row를 tombstone으로 먼저 옮깁니다.
  - 신규 `ontology_lease_tombstone(..., ended_at_ms, end_reason)`.
  - `active_ontology`, `generation_hwm`, `projection_idempotency`는 column 변경이 없습니다.
- **trigger** (모든 writer에 적용):
  - T1: high-water UPDATE는 증가할 때만 허용합니다. T2: high-water DELETE는 거부합니다.
  - T3: lease INSERT는 `h.high_water = NEW.fencing_token AND h.minted_for = NEW.lease_id`일 때만 허용합니다. main과 xsu Python은 `minted_for`를 쓰지 않으므로 fail closed가 됩니다.
  - T4: lease의 `fencing_token` UPDATE는 거부합니다.
  - T5: `active_ontology` UPDATE는 `epoch = OLD.epoch + 1`, `generation = OLD.generation`, `NEW.fencing_token = hw`, `minted_for LIKE 'activate:%'`일 때만 허용합니다. legacy caller-chosen publish를 거부해 B의 wedge를 닫습니다.
  - T6: `active_ontology` INSERT도 같은 mint 조건을 요구하고, generation은 `generation_hwm` 이하여야 합니다.
  - T7: `generation_hwm` 감소와 DELETE는 거부합니다.
  - T8: `projection_idempotency` DELETE는 거부합니다 (pending row 자동 삭제 금지, SEMANTIC_CONTROL_PLANE.md:362-364).
- **connection:** Owner connection에 WAL, `busy_timeout=10000`, `synchronous=FULL`을 명시합니다. 현재 seochod는 rusqlite의 암묵적 5 s timeout을 씁니다.
- **`retired_ontology`:** P4에서 같은 파일로 편입합니다. 그때까지는 Python 소유로 둡니다.

---

## 5. 불변식과 강제 지점

| # | 불변식 | 강제 지점 | 테스트 |
|---|---|---|---|
| I1 | token은 `(ws, pkg)`별로 strictly increasing이고 durable하며, release/expiry/prune/restart/activate/mixed writer 상황에서도 재사용되지 않음 | type: `FencingToken`은 `WriteTxn::mint`에서만 생성. 계산은 `max(hw, live, pointer)+1`. DB: T1-T6, `UNIQUE(ws, pkg, token)`. migrate: hw ≥ DozerDB floor | seeded xorshift model test (10k sequence, reopen 포함). 8-process acquire/release (xsu concurrent test 이식). **legacy-writer replay**: main :223 SQL과 xsu :299-315 SQL을 v2 DB에 실행해 abort 확인 (Rust raw rusqlite와 Python subprocess 둘 다). floor seed test |
| I2 | `(ws, pkg, purpose)`당 held lease는 최대 1개 | Authority acquire (만료 row tombstone 이동 후 INSERT), `UNIQUE(ws, pkg, purpose)` | room:tests/seocho/test_ontology_lifecycle.py:20 이식, model assertion |
| I3 | pointer는 `(generation, epoch)` CAS. publish마다 epoch +1, generation은 증가만. pointer token은 mint된 값이므로 이전의 모든 lease token보다 큼 | `activate`/`recreate`, T5-T7 | test_active_pointer.py 8건 이식. 기존 "stale token" case는 `min_token` assert로 재작성. publish 이전 lease는 `check`가 실패함을 model로 확인 |
| I4 | activation token은 authority만 고름 | `activate`는 token을 받지 않고 `min_token`만 받음. T5/T6 | `--fencing-token > hw`는 `fenced`이고 아무것도 쓰지 않음. legacy Python `publish` UPDATE가 abort됨 |
| I5 | renew/release는 owner 일치가 필요. renew는 TTL 범위, 미만료, 불변 pointer를 요구. expiry는 authority clock 하나만 사용 | Authority write txn, `Clock` | fake-clock unit test, publish 후 renew가 `stale_pointer` |
| I6 | `purpose=projection` lease만 projection capability를 얻고, precommit에서 재검증됨 | `admit`, `check` (main.rs:218-279 규칙 이식), seochod `recheck` 위치 유지 (main.rs:801-811) | main.rs:1096, :1281, :1333, :1350을 `LifecycledVerifier`와 fixture daemon으로 재지정 |
| I7 | single writer | flock, socket dispatch, `lifecycle_meta.writer` (facade는 socket 없이 write 거부), trigger | 두 번째 `serve`는 "lock held"로 종료. v2 DB에서 socket 없는 facade write는 raise |
| I8 | 기존 idempotency state machine 유지 | `idem_claim`/`idem_complete` (main.rs:288-358 이동), T8 | main.rs:1196, :1436, :1448, :1470, :1487 이동 |
| I9 | authority clock은 뒤로 가지 않음 | `now = max(SystemTime, last_wall_ms)`, 매 write txn마다 persist | 뒤로 가는 fake clock |
| I10 | token scope = stamp scope | capability `pkg`가 `$fence_scope`로 그대로 전달 (main.rs:461-468). workspace 분리는 MERGE key (main.rs:420, :430) | contract fixture: `capability.pkg`가 `$fence_scope`와 같음. 다른 workspace의 같은 id가 비교되지 않음 |
| I11 | writer는 mint할 수 없음 | seochod는 `-proto`만 의존 | CI: `cargo tree -p seochod`에 `seocho-lifecycle`(authority crate)이 나오면 실패. `compile_fail` doctest |
| I12 | 언어 간 wire와 의미 drift 없음 | golden fixture `crates/seocho-lifecycle-proto/contract/v1/*.json`, `hello.protocol` major 검사 | Rust와 Python 양쪽에서 fixture replay. P2-P3에는 **differential scenario**: 같은 시나리오 파일을 legacy Python backend와 lifecycled backend에 돌려 관측 결과가 같은지 확인 |
| I13 | schema 무결성 | 시작 시와 `hello`에서 `ddl_sha256` 확인 | trigger를 drop한 뒤 start하면 `schema_tampered` |

---

## 6. 장애 모드와 복구

| 장애 | 영향 | 복구와 근거 |
|---|---|---|
| lifecycled crash | 진행 중 txn은 commit되었거나 일어나지 않음 (WAL). 커널이 flock을 해제 | supervisor 재시작. stale socket은 기존 로직으로 정리. client는 같은 `client_op_id`로 retry해 orphan lease가 생기지 않음 |
| lifecycled down | 모든 governed path가 fail closed: acquire, renew, activate, admit, projection | pin read는 ro SQLite로 계속 동작. 다운타임이 TTL×2/3 미만이면 `LeaseKeeper`가 견딤. 이것이 **새 SPOF**이며 수용한 비용임 (§8). projection은 이미 seochod가 SPOF임 (room:store/graph.py:620-623) |
| mint 직후 crash | 같은 txn이므로 hw 증가와 INSERT가 함께 rollback. gap은 생길 수 있으나 reuse는 불가능 | 없음 |
| seochod crash (projection 중) | pending claim이 남음 | `SEOCHOD_PENDING_RECLAIM_MS` 후 reclaim (main.rs:323-324). 자동 삭제 없음 (T8) |
| commit 성공, `idem_complete` 실패 | `ok=false`, "committed but unrecorded" | 현행 동작 유지 (main.rs:829-834) |
| Python holder crash | lease가 TTL까지 남음 | 다음 acquire가 tombstone으로 이동. `read_admit`은 row가 없으므로 query 60 s blackout이 사라짐 |
| 장기 run 중 lease 상실 | 다음 batch가 `lease_expired`/`stale_pointer` | 현재는 candidate_stage.py:143의 raise만 있음. `LeaseKeeper`가 원인을 명시함 |
| legacy Python writer가 v2 DB에 접근 | acquire, activate, recreate가 `sqlite3.IntegrityError` (trigger ABORT) | **의도된 fail closed**. 먼저 `audit`을 돌리고 배포 단위로 cutover |
| wall clock 점프 | 뒤로 가면 I9로 clamp, 앞으로 가면 조기 만료 (fail closed) | `writer_ts`는 `SEOCHOD_MAX_CLOCK_SKEW_MS` 유지 (main.rs:691-699) |
| seochod와 run이 다른 DB를 봄 | 잘못된 authority로 검증 | preflight가 seochod `health` 안의 lifecycled `db_uuid`와 spec `state_db`의 `lifecycle_meta.db_uuid`(ro로 읽음)를 비교. 현재 공백 (room:run_preflight.py:370-383) |
| trigger 수동 삭제 | 무결성 상실 | `ddl_sha256` 불일치 시 serve 거부 |
| disk full, I/O error | txn abort, `internal` | 부분 적용 없음 |
| SQLite on NFS | 무결성 보장 불가 | 문서화만 함. forbid(unsafe) 하에서 statfs 검사는 불가 |

**hot-path 지연** (추정치, P2 benchmark gate에서 측정):
- index batch admission: capability를 lease당 1회 받아 cache하므로 batch당 0 RTT입니다. per-batch 검사를 유지하면 1 RTT(약 0.1-0.3 ms)입니다.
- projection: 3 RTT(약 0.5 ms)가 추가되지만, fsync 2회는 현재 seochod도 이미 하고 있습니다.
- online query: write txn 4회 대신 read RTT 1회입니다.
- `ask` pin: 변화 없음.

---

## 7. 마이그레이션 및 단계별 롤아웃

공통 제약: 진행 중인 다른 Python 작업과 충돌하지 않도록 합니다.
- Python 변경은 별도 branch에서 하고, 다음 파일만 수정합니다: `lifecycle.py`, `active_pointer.py`, `online_query_admission.py`, `projection_receipt.py`, `run_preflight.py`, `e2e.py`, `cli/ontology.py`, 신규 `lifecycle_client.py`.
- 다른 작업과 겹치는 `candidate_stage.py`, `local_engine.py`, `store/graph.py`, `index/pipeline.py`는 바꾸지 않은 API를 통해서만 영향을 받습니다.

| Phase | 내용 | 진입 기준 | 종료 기준 | compat shim, rollback |
|---|---|---|---|---|
| **P0** 거버넌스 | (a) **sr-x9x**: xsu를 main에 병합 (maintainer 담당). (b) ADR-0163:20/:60-63 개정 ADR: policy는 Python, invariant kernel은 Rust. ADR-0155 측정 gate에 correctness 예외 명시. authority ≠ writer 명시. (c) **sr-5zg**: Rust workspace를 공개 저장소로 upstream. (d) 신규 이슈 등록 | — | ADR 승인, xsu 병합, upstream 완료 | 문서뿐이라 rollback 대상 없음 |
| **P1** library + audit | 3 crate, schema v2, trigger, `Authority`, model/legacy-replay test, golden fixture, `audit`, `migrate --dry-run`. 별도 PR: seochod test fixture를 `schema::create_for_test`로 전환 (DDL 복제 제거, main.rs:1106-1114, 1233-1247) | P0 (b) 초안 공개 | CI green. 운영 DB에서 `audit --strict` 0건 (중복 token, hw < max = violation; expired-but-held = warning이지만 `--strict`에서 실패로 집계, sr-tmz.1 README) | production 변경 없음 |
| **P2** lifecycled opt-in | `serve`. `migrate`는 `VACUUM INTO` backup 후 한 txn으로 실행하고, floor 또는 attest가 필수 (`seochod fence-floor --scope ws/pkg`: read-only Bolt `max(_fencing_token)`를 node와 rel에서 구해 `--seed-floor`로 전달). Python `LifecycleClient`/`LeaseKeeper`/facade 스위치. CLI `--lifecycled-socket`. `--fencing-token`은 assert-only. seochod는 **변경 없음** (`SqliteReadVerifier`) | P1 종료, sr-x9x 완료 (v0 DB는 거부) | 기존 Python test 약 37건(main)/약 49건(xsu)이 legacy backend와 lifecycled backend 양쪽에서 green. differential scenario 일치. kill -9 chaos (acquire, activate). latency gate 측정 | `downgrade --to-schema 1`: trigger, unique index, tombstone 제거, hw 보존. 이후 xsu Python이 `max(hw, live, ptr)+1`로 안전하게 재개. **v0로의 downgrade는 거부** |
| **P3** seochod pure client | ADR-0163:62-63을 지키기 위해 PR 2개로 분리: (a) lifecycled writer.sock ops. (b) seochod `LifecycledVerifier`, `Admitted`/`WriteScope::governed`, `health`에 lifecycled `hello` 포함, preflight `db_uuid` 비교. `online_query_admission`을 `read_admit`으로 전환 | P2가 1개 배포 단위에서 soak | governed FinDER run 1회, projection 중 kill -9 chaos green. seochod가 control DB를 열지 않음 | `SqliteReadVerifier`를 `SEOCHOD_CONTROL_DB` 뒤에 1 release 동안 유지. 같은 table이므로 호환 |
| **P4** 은퇴 | Python write SQL과 DDL 삭제 (ro read만 유지). shim 제거. `retired_ontology`와 reclamation 편입. 구 `dataplane/seochod` 삭제. `pipeline.py:889` key 수정 (owner 합의 후) | P3 1 release 경과 | `src/seocho/ontology`에 `INSERT INTO ontology_lease`나 `_next_fencing_token`이 없음을 test로 확인 | binary와 wheel을 이전 release로 되돌림. schema는 v2 유지 |
| **P5** 조건부 multi-host | `PostgresBackend` (postgres_sequence_v2.py:194-209 패턴) 또는 etcd. TCP+mTLS. 이때 Ed25519 signed capability 도입 | multi-host 요구 확정 | fencing/failover chaos 재실행 (SEMANTIC_CONTROL_PLANE.md:368-372) | backend 단위 rollback |

**이슈 매핑 (`sr-*`)**

| 이슈 | 관계 |
|---|---|
| sr-x9x (P1, open) | P2 진입의 hard prerequisite. migrate가 v0를 거부함 |
| sr-5e6 (P3, open) | P2-P3에서 해소: mixed minter는 T3/T5/T6로 abort, `admit`이 purpose 검사, 구버전 DB의 read-only open은 `audit`(ro)과 v1 read 지원으로 해결 |
| sr-sxl (P2, open) | P3 문서에 흡수: daemon 2개, socket 3개, socket dir, `SEOCHOD_ALLOW_UNGOVERNED` |
| sr-5ev (P2, open) | 같은 실패 유형 ("native가 build되지 않아 fallback으로 조용히 측정됨"). lifecycled fixture binary가 없으면 CI는 **loud fail**, local에서만 skip. fallback backend는 두지 않음 |
| sr-mz6 (P3, open) | parity test가 조용히 통과하는 문제와 같은 유형. differential scenario가 backend id를 기록하고 assert함 |
| sr-5zg (P2, open) | contract fixture와 crate version pin의 전제 |
| sr-xsu (closed) | 그 contract가 I1이 됨. main.rs:196-200 주석은 P3에서 `Admitted`로 대체 |
| 신규 등록 대상 | (1) epic "seocho-lifecycled authority" (P1-P4 하위). (2) 구 `dataplane/seochod` 은퇴. (3) room:index/pipeline.py:889 `fence`/`fencing_token` key 오류. (4) client `idempotency_key`가 매 호출 새 uuid라 retry 시 dedupe 안 됨 (room:src/seocho/dataplane/seochod.py:35-36, store/graph.py:652-660). (5) 30 s client timeout과 최대 10 min governed txn의 불일치 |

---

## 8. 리스크 / 열린 질문

### 리스크

1. **새 SPOF와 운영 부담.** daemon과 socket이 하나씩 늘고 supervisor가 필요합니다. socket activation은 forbid(unsafe) 때문에 불가합니다. 완화책은 `client_op_id`, `LeaseKeeper`, 빠른 재시작(추정 < 100 ms)입니다.
2. **trigger의 hard fail.** v2로 migrate한 뒤 업그레이드되지 않은 Python(xsu 포함)은 abort됩니다. 부분 rollout에서는 이것이 outage처럼 보입니다. `audit`, release note, 배포 단위 cutover로 대응합니다.
3. **single-uid dev box에서는 privilege 분리가 process 경계로 축소됩니다.** 3-socket 권한 분리는 appliance에서 OS user를 분리할 때만 의미가 있습니다.
4. **ADR 승인이 지연되면 P2 이후가 막힙니다.** P1은 production 변경이 없어 승인 없이도 진행할 수 있습니다.
5. **DozerDB floor 생략.** `--no-dozerdb-floor-i-attest`로 진행했는데 실제로 v0 재사용 token이 stamp되어 있었다면 I1이 깨질 수 있습니다. 이는 xsu가 인정한 손실(xsu:lifecycle.py:21-37)과 같습니다.
6. **model checker 부재.** proptest와 loom이 없어 Owner thread 내부 interleaving은 검증하지 않고 가정합니다. 단일 Owner 설계가 가정의 범위를 줄입니다.
7. **projection당 IPC 3회와 seochod의 runtime dependency.** P2 gate에서 plane별 시간을 측정합니다 (ADR-0163:64-66).

### 열린 질문

1. activate에 `activation` purpose lease를 요구할지, operator.sock 권한과 CAS만으로 충분할지 (policy 사안, Python 결정).
2. `read_admit`(row 없음)이 scripts (okx_text2cypher_live.py:96, run_governed_memory_calibration.py:113,133)의 감사 요구를 충족하는지, query admission의 durable record가 필요한지.
3. per-batch Python admission을 남길지, cached capability와 precommit `check`만으로 갈지.
4. agent-memory projection (room:agent_projection.py:69-76)이 governed seochod를 통과할 때 ontology admission을 어디서 받는지. 두 fencing domain이 같은 DozerDB에서 만납니다.
5. appliance의 OS user, group 구성 (seocho, seocho-lc, seochod)과 systemd unit 형태. 현재 seochod를 spawn하는 코드는 없습니다.
6. `max_ttl_ms` 기본값 (3600 s 제안)과 tombstone retention 기간.

---

## 9. 첫 번째 구현 단위 제안

**PR-1: `seocho-lifecycle` library와 read-only `audit`.** production 경로 변경이 없고 Python package 변경도 없습니다.

범위:
- `crates/seocho-lifecycle-proto`: `ErrorCode`, `ObservedToken`, `Capability` serde type만 포함합니다.
- `crates/seocho-lifecycle`:
  - `schema::{apply_v2, create_for_test, ddl_sha256}`
  - trigger T1-T7
  - `FencingToken`과 `WriteTxn::mint` (`max(hw, live, pointer)+1`, `minted_for`)
  - `SqliteBackend`의 `acquire`/`release`/`activate`
  - `Clock` trait
- `crates/seocho-lifecycled`: subcommand는 `audit <db>` 하나입니다. `mode=ro`로 열고 다음 위반을 출력합니다: 중복 token, hw < max(lease, pointer), 만료된 held row. v0, v1, v2 모두 지원합니다.
- 테스트:
  1. seeded xorshift model test: acquire/release/activate/reopen 10k sequence에서 scope별 strictly increasing.
  2. **legacy-writer replay**: room:lifecycle.py:223의 SQL과 xsu:lifecycle.py:299-315 + :365의 SQL을 v2 DB에 실행해 trigger abort 확인.
  3. legacy UPDATE publish (caller-chosen token)가 T5로 abort되는지 확인 (B wedge 회귀 방지).
  4. `audit`이 v0 fixture의 재사용 token을 검출하는지 확인.
  5. `ddl_sha256`이 trigger drop을 검출하는지 확인.
  6. `compile_fail` doctest: crate 밖에서 `FencingToken` 생성 불가.

이 PR로 증명하는 것:
- sr-xsu contract가 주석 (main.rs:196-200)에서 type, trigger, test로 옮겨갑니다.
- 어떤 언어의 legacy writer도 v2 DB에서 token을 중복 발급할 수 없습니다 (sr-5e6 hazard 제거).
- 운영자는 오늘 당장 기존 Python-owned DB의 위반 여부를 read-only로 점검할 수 있습니다.

종료 기준: `cargo test --offline -p seocho-lifecycle -p seocho-lifecycled` green, clippy workspace lint green, 운영 control DB에 대한 `audit` 결과를 이슈에 첨부.