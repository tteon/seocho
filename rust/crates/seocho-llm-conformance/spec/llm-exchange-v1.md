# `seocho.llm_exchange.v1`: LLM HTTP exchange record (normative)

Status: v1. Parent design: `docs/design/llm-io-contract.md` §7.1, §7.2, §7.7, §8 (C10, C15), §11 PR-1.

The key words MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119.

| Artifact | Path |
|---|---|
Paths are relative to the repository root.

| Artifact | Path |
|---|---|
| This spec | `rust/crates/seocho-llm-conformance/spec/llm-exchange-v1.md` |
| JSON Schema (draft 2020-12) | `rust/crates/seocho-llm-conformance/spec/llm-exchange-v1.schema.json` |
| Golden fixtures (one record per file) | `rust/crates/seocho-llm-conformance/spec/fixtures/*.json` |
| Expected results | `rust/crates/seocho-llm-conformance/spec/expected.json` |
| sk-llm/1 source rows of converted fixtures | `rust/crates/seocho-llm-conformance/spec/sources/*.sk-llm-1.json` |
| Reference implementation (stdlib Python) | `rust/crates/seocho-llm-conformance/python/exchange_v1.py` |
| Fixture generator | `rust/crates/seocho-llm-conformance/python/make_exchange_fixtures.py` |
| Python tests | `rust/crates/seocho-llm-conformance/python/test_exchange_v1.py` |
| Rust consumability test | `rust/crates/seocho-llm-conformance/tests/llm_exchange_v1_fixtures.rs` |

`seocho-llm-conformance` is a workspace member that is test-only for now (empty library, dev-dependencies only); the replay/report tooling of the design grows inside it later.

`sk-llm/1` is the JSONL format of the Python recording transport (`RRTransport`) that produced the recordings this spec was developed against; that harness is not part of this repository. It exists in two generations: **legacy** rows store the parsed response dict, **raw-generation** rows additionally store the response bytes (`body_utf8`/`body_b64`, `body_sha256`), headers, `redaction` and a `rec_id`.

## 1. Unit of record

One record = **one HTTP attempt** (or one transport failure in place of an HTTP response) to an LLM endpoint. Retries, re-prompts, downgrades, fallbacks and SDK-internal retries are separate records linked through `chain`. A record is one JSON object; corpus files are JSONL (one record per line), committed fixtures are one pretty-printed record per file.

Records are immutable. A wrong record is never edited; it is tombstoned (design §7.1 corpus layout).

## 2. Record shape

Every key below MUST be present (use `null` where allowed). Objects marked *closed* MUST NOT carry other keys. `request.body` and `usage` are open objects.

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | `"seocho.llm_exchange.v1"` | |
| `rec_id` | `sha256:<64 hex>` | run-scoped observation id, §4 |
| `captured_at` | string \| null | UTC `YYYY-MM-DDTHH:MM:SSZ` of the attempt start |
| `source` | closed object | where the record came from |
| `source.kind` | enum | `canary`, `sweep`, `onboard`, `prod-shadow`, `import:sk-llm/1`, `import:finder-v1`, `import:room-diagnostics`, `synthetic`, `synthetic-derived` |
| `source.run_id` | non-empty string without `\|` | **replay scope** that produced the attempt: `(run_id, logical_call_id)` MUST identify exactly one logical call (§5). sk-llm/1 imports use `run_id/arm/session_id` (§10) |
| `source.git_sha`, `source.llm_io_version` | string \| null | recorder build; null for imports |
| `source.wire` | bool | `true` only when the bytes came off a real provider socket. Scripted, synthetic and harness-re-serialized blobs are `false` and are excluded from profile-suggest evidence |
| `source.served_from` | `live`\|`scripted`\|`replay`\|`cache`\|null | sk-llm/1 provenance; `replay`/`cache` rows are reuse records, not observations, and MUST have `wire:false` |
| `source.origin` | closed object \| null | `{format, path, file_sha256, line?, source_rec_id?, source_run_id?, arm?, session_id?, role?}`. REQUIRED for `import:*` kinds, with non-null `path` and `file_sha256` (the `sha256:` of the original file as stored, e.g. the `.jsonl.gz` bytes) (S10). `source_run_id`/`arm`/`session_id`/`role` keep the source format's own identity fields (sk-llm/1) |
| `endpoint` | closed object | `{provider, host, path, catalog_snapshot}`; `catalog_snapshot` is `sha256:` of the `/v1/models` snapshot, null until `llmconf catalog` exists |
| `model` | string | requested model id |
| `case` | closed object \| null | `{case_id, task, input_class, contract_sha256, wire_dialect, profile_sha256, enforcement}` |
| `case.input_class` | `public`\|`synthetic`\|`private`\|null | committed tier admits only `public`/`synthetic` (C12). null = unclassified = private tier only |
| `case.contract_sha256`, `case.profile_sha256` | 64 hex \| null | **reserved**: design §5.4 hashes. MUST be null until M2 produces contracts/profiles; never invented |
| `case.wire_dialect`, `case.enforcement` | string \| null | reserved, nullable in M1 |
| `chain` | closed object | `{logical_call_id, attempt, next_step_of, step_kind, agent}` |
| `chain.logical_call_id` | non-empty string without `\|` \| null | caller's logical call, unique within `source.run_id`; null only for imports that lack it |
| `chain.attempt` | int ≥ 0 | 0-based HTTP attempt within the logical call |
| `chain.next_step_of` | `sha256:` \| null | `rec_id` of the attempt whose planned next step this attempt is; null for attempt 0 and when unknown (imports) |
| `chain.step_kind` | enum | `initial`, `retry`, `reprompt`, `downgrade`, `fallback`, `sdk_retry` (C15: an SDK-internal retry observed at the transport) |
| `chain.agent` | non-empty string \| null | harness agent; part of the import replay key (§5) and, when `logical_call_id` is null, of the `rec_id` material (§4) |
| `request` | closed object | |
| `request.body` | object \| null | sanitized request body (no credentials; `Authorization` is a header and is never stored) |
| `request.body_scope` | `full`\|`subset`\|`none` | `subset` = only some keys kept (sk-llm/1 keeps the hashed fields; legacy rows replace `messages` with a digest) |
| `request.messages_sha256` | 64 hex \| null | digest of `messages` when they were not stored |
| `request.req_hash`, `request.req_hash_scheme` | `sha256:` \| null, enum \| null | §7 |
| `request.occurrence` | int \| null | index among identical `(agent, req_hash)` requests in the run (import replay key) |
| `response` | closed object | §3 |
| `timing` | closed object | `{latency_ms, start_ns}` |
| `usage` | object \| null | provider `usage` as received (completion/prompt/reasoning tokens) |
| `live_outcome` | `{verdict, codes[], fingerprint}` \| null | `seocho-llm-io` verdict at record time; null until the conformance tooling records it |
| `oracle_outcome` | same \| null | Python oracle verdict written by importers (design §7.7) |
| `redaction` | closed object | §8 |

## 3. Response body

| Field | Rule |
|---|---|
| `http_status` | int 100..599, or null exactly when `transport_error` is set (S4) |
| `headers` | **allowlist only**: `content-type`, `x-request-id` (lower-case keys, string values). Anything else (`set-cookie`, org/account ids) MUST NOT be stored (S6) |
| `body_utf8` / `body_b64` | the stored response bytes. If the bytes are valid UTF-8 they MUST be in `body_utf8`, else standard padded base64 in `body_b64`. At most one is non-null (S2) |
| `body_sha256` | `sha256:` + hex sha256 of exactly the stored bytes (UTF-8 encoding of `body_utf8`, or the base64-decoded bytes). Non-null exactly when a body is stored (S2, S3) |
| `body_fidelity` | `raw` = bytes as read from the transport (`upstream.read()`), never parsed and re-serialized (C10). `reserialized` = bytes produced by re-serializing a parsed value (all legacy sk-llm/1 data). null exactly when no body is stored |
| `truncated` | `true` only for `reserialized` bodies that are a prefix of the original (S5) |
| `transport_error` | `{class: timeout\|transport_error, detail}` when no HTTP response exists; then `http_status` and both bodies are null (S4) |

Rules of use:
- C10 and every byte-dependent fixture (key order, duplicate keys, number spelling, non-JSON truncation) MUST reference `raw` records with `replay_class: full` only.
- `reserialized` records MAY be used for structural T/E/P judgement (carrier, finish, error class, tool-argument encoding).
- `body_fidelity` describes serialization provenance, not redaction. A `raw` body that was byte-level scrubbed stays `raw` and is identified by `redaction.applied` and `replay_class: none` (this is how the raw-generation sk-llm/1 recorder writes it). The "original bytes" guarantee of C10 therefore holds for `raw ∧ replay_class = full`.

## 4. Identity: `rec_id`

```
rec_id = "sha256:" + hex( sha256( utf8( run_id "||" logical_call_id "||" attempt "||" body_sha256 ) ) )
```

When `chain.logical_call_id` is null (imports only), the fallback observation identity of §5 is appended:

```
rec_id = "sha256:" + hex( sha256( utf8( run_id "||" "" "||" attempt "||" body_sha256
                                        "||" req_hash "||" occurrence "||" agent ) ) )
```

- `run_id` = `source.run_id`, `logical_call_id` = `chain.logical_call_id`, `attempt` = `chain.attempt` in decimal, `body_sha256` = `response.body_sha256` **including its `sha256:` prefix**; in the fallback form `req_hash` = `request.req_hash` (with prefix), `occurrence` = `request.occurrence` in decimal, `agent` = `chain.agent`.
- Records with a `logical_call_id` use the four-part form only; the fallback parts are never appended to them.
- A null component is the empty string. A transport error (no body) therefore ends in `"||"`.
- The join is injective because `source.run_id` and `chain.logical_call_id` MUST be non-empty and MUST NOT contain `|` (schema pattern `^[^|]+$`); `attempt` and `occurrence` are decimal integers and `body_sha256`/`req_hash` fixed-shape tags. `logical_call_id` null (imports only) and the forbidden `""` therefore never meet, so the four-part and fallback forms cannot collide. `agent` is the only component that may contain `|`; it is last, so it cannot shift the other boundaries, and it MUST be non-empty when present (null and `""` would otherwise both be the empty string).
- `rec_id` is unique per observation **because** `(run_id, logical_call_id)` names one logical call (§5), and, for records without `logical_call_id`, because its material contains the whole fallback replay key `(run_id, agent, req_hash, occurrence)`: two records with distinct replay keys always have distinct `rec_id`s. A writer whose logical-call counters restart per arm, session or agent MUST put that scope into `source.run_id`; it MUST NOT reuse one `run_id` across them. Repeating the same request in another run, or the same body at another attempt, yields a different `rec_id`. Two records with the same `rec_id` in one corpus are a writer/importer defect: the import is rejected and the scope fixed, never resolved by dropping one of two genuine observations (§6).
- The raw-generation sk-llm/1 recorder (`rec_id()`) evaluates this expression with the sk-llm/1 run scope of §10 as `run_id`, so `convert_sk_llm1` preserves the `rec_id` of every row it writes with a `logical_call_id` (live, scripted, replay and cache rows, including rows served from a pre-raw source; verified against that recorder), unless the converter itself alters the stored body (402 billing redaction, §8). Rows without `logical_call_id` get the fallback form, so their v1 `rec_id` differs from the row's own.
- Converters MUST recompute `rec_id` from the v1 fields. When a source row carried its own `rec_id` it is kept as `source.origin.source_rec_id`; for raw sk-llm/1 rows with a `logical_call_id` the two are equal.

## 5. Replay key (run-scoped)

- The replay key is **`(source.run_id, chain.logical_call_id, chain.attempt)`**. Lookup is scoped to one run; a replay MUST NOT be served from another run. Within a corpus the key MUST be unique (one record per key); importers reject a file that violates this.
- `source.run_id` is the replay scope, not necessarily the harness's run label. sk-llm/1 shows why: one `run_id` covers the `iso`, `shared` and `single` arms (and coordinator/preflight sessions), and `logical_call_id` is a per-agent counter (`A3#1`) that repeats in every arm. In the 1,230 recorded rows this spec was developed against (not published here), the bare `(run_id, logical_call_id, attempt)` has 412 duplicates, 348 of them with different bodies; replaying by it would serve another arm's response. The import scope `run_id/arm/session_id` makes all 1,230 keys and `rec_id`s unique.
- Only records without `logical_call_id` (imports) use the fallback key **`(source.run_id, chain.agent, request.req_hash, request.occurrence)`**, the RRTransport in-run rule. The same components are part of their `rec_id` material (§4), so `rec_id` is unique whenever this key is.
- Global keys such as `(model, req_hash, 0)` across runs MUST NOT be used.
- A missing key is `ReplayMiss`, fail closed (C11). A hit whose `redaction.replayable` is `false` is `NotReplayable`, also fail closed. Lenient lookup is forbidden in tests.
- `replay_class: carrier_intact` hits replay with the fingerprint narrowed to the `tool_calls`/`content` carriers, finish and HTTP class (§8).

## 6. Body store vs observations

- **Bodies are content-addressed**: a stored body lives at `bodies/<hex[0:2]>/<hex>` where `hex` is `body_sha256` without its prefix. Identical bytes are stored once (deduplicated).
- **Observations are never deduplicated**: every HTTP attempt is its own record (its own `rec_id`), even when request and body are identical to an earlier one (T=0 repeats on MARA are common). Rates, Wilson intervals and every reliability statistic count observation records; deduplication applies to body storage only.
- `served_from: replay|cache` rows (sk-llm/1 reuse) are not observations and are excluded from rates.
- A record is validated with its body inline (`body_utf8`/`body_b64`). How a corpus writer externalizes inline bodies into the store and resolves them back is the importer's concern; the resolved record MUST satisfy S2/S3.
- Both the body store and observation files are append-only.

## 7. `req_hash`

`req_hash = "sha256:" + sha256( scrub( canonical_json( {k: body[k] for k in FIELDS if k in body} ) ) )`, with `canonical_json = json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)` and `scrub` = the sk-llm/1 recorder `SCRUBBERS` (`exchange_v1.HASH_SCRUBBERS`) (ISO timestamps, `_writer_ts`), applied to the hash input only.

| `req_hash_scheme` | FIELDS |
|---|---|
| `sk-llm/1` | `model, messages, response_format, tools, tool_choice, max_tokens, temperature` (sk-llm/1 recorder `HASH_FIELDS`) |
| `llm_exchange.v1` | the above + `extra_body, chat_template_kwargs, reasoning_effort` (design §7.1) |

When a body has none of the three extra keys both schemes give the same hash. Imported sk-llm/1 rows keep their hash and are tagged `sk-llm/1`.

## 8. Redaction `rec-redact/1` and replay class

`redaction = {policy: "rec-redact/1", applied: [...], replay_class, replayable}`.

`applied` lists named tokens and/or RFC 6901 JSON pointers (into the parsed response body) of replaced fields:

| Token | Meaning | Alters body |
|---|---|---|
| `auth_header_dropped` | request `Authorization` not stored (every live request) | no |
| `headers_filtered` | response headers outside the allowlist dropped | no |
| `request_messages_digested` | `request.messages` replaced by `request.messages_sha256` | no |
| `body_secret_scrubbed` | Bearer / `sk-` regex (`exchange_v1.BEARER_RE` / `SK_RE`) replaced inside the body | **yes** |
| `body_sensitive_key_redacted` | a `SENSITIVE` key's value replaced in the parsed body | **yes** |
| `body_billing_redacted` | the value (any JSON type) of every member whose **decoded** name is `billing_portal_url` / `balance_units` (402 billing errors), at any depth and including duplicate keys, replaced by `"[redacted]"` at the byte level (the key's own spelling, e.g. a `\uXXXX` escape, is kept); `convert_sk_llm1` applies it to both sk-llm/1 generations | **yes** |
| `body_truncated` | body cut by the recorder (sk-llm/1 `unparsed_body[:4000]`) | **yes** |

| `replay_class` | `replayable` | Condition |
|---|---|---|
| `full` | `true` | body not altered: no `body_*` token, no pointer |
| `carrier_intact` | `true` | only fields outside the carriers replaced; `applied` lists ≥ 1 pointer and every pointer is one of `/choices/N/message/reasoning`, `/choices/N/message/reasoning_content`, `/usage/completion_tokens_details/reasoning_tokens`, `/error_model_output`. Replay narrows the fingerprint to `tool_calls`/`content`, finish and HTTP class |
| `none` | `false` | carrier replaced, secret scrubbed, billing redacted, truncated, or no body stored for an HTTP response. Metrics only |

Committed tier (`exchange_v1.validate(record, tier="committed")`, on top of S1-S13):

| # | Rule |
|---|---|
| C1 | `request.body.messages` present ⇒ `case.input_class` is `public` or `synthetic` (C12; unclassified/private prompts stay private) |
| C2 | no member whose decoded name is `billing_portal_url` / `balance_units` (any depth, every duplicate) has a value other than `"[redacted]"` in the stored body; a value that cannot be decoded (truncated body) counts as unredacted |
| C3 | `body_secret_scrubbed` / `body_sensitive_key_redacted` only on `synthetic`/`synthetic-derived` records; such records from real runs stay in the private tier |

Fixture 07 is synthetic and shows only the post-scrub bytes.

## 9. Semantic rules

The JSON Schema encodes shape and the conditionals it can express. The reference validator (`exchange_v1.validate`) enforces all of the following; S1, S3, S8's pointer allowlist, S11, S12 and S13 are validator-only (the schema covers S10's origin requirement).

| # | Rule |
|---|---|
| S1 | `rec_id` equals the §4 recomputation (four-part form, or the fallback form when `logical_call_id` is null) |
| S2 | at most one of `body_utf8`/`body_b64`; `body_sha256` and `body_fidelity` non-null exactly when a body is stored; valid UTF-8 MUST use `body_utf8` |
| S3 | `body_sha256` equals sha256 of the stored bytes; `body_b64` is valid base64 |
| S4 | `transport_error` ⇒ `http_status` null and no body; `http_status` null ⇒ `transport_error` |
| S5 | `truncated` ⇒ `body_fidelity: reserialized`; `truncated` ⇔ `applied` contains `body_truncated` |
| S6 | response header keys ⊆ {`content-type`, `x-request-id`} |
| S7 | `replayable` = (`replay_class` ≠ `none`) |
| S8 | any `body_*` token ⇒ `none`; `full` lists no pointer; `carrier_intact` lists ≥ 1 pointer, all from the §8 allowlist |
| S9 | `attempt` 0 ⇔ `step_kind: initial`, and attempt 0 has `next_step_of: null` |
| S10 | `import:*` ⇒ `source.origin` non-null with non-null `path` and `file_sha256`; `served_from` replay/cache ⇒ `wire:false` |
| S11 | `request.body` null ⇔ `body_scope: none`; `req_hash` null ⇔ `req_hash_scheme` null |
| S12 | an HTTP response (no `transport_error`) without stored body ⇒ `replay_class: none` |
| S13 | secret scan, on raw text **and** decoded values: no string anywhere in the record (keys and values, including `body_utf8`, request messages, nested `extra_body`, header values, `usage`, `transport_error.detail`), no text view of the stored body (UTF-8/latin-1, plus UTF-16/UTF-32 when JSON encoding detection finds it), no decoded key or string value of the parsed response body (any depth, every duplicate key; so JSON `\uXXXX` escapes cannot hide a credential), and no decoded string of a JSON document embedded in such a string (tool-call `arguments`, stringified payloads; up to 3 levels) matches the recorder's `sk-` / Bearer regexes. No `SENSITIVE` member (decoded name, any depth, every duplicate) of `request.body`, `usage` or the parsed response body holds a value that can carry material: only `"[redacted]"`, `""`, `null`, a boolean, `[]` or `{}` are allowed; any other string, a number, or a non-empty array/object is rejected. Visible scrub footprints in the body (`Bearer [redacted]` / `sk-[redacted]` in raw text or a decoded string, a `SENSITIVE` member = `"[redacted]"`, a billing member = `"[redacted]"`, names and values decoded) require the matching `body_*` token in `applied` (and so, by S8, `replay_class: none`) |

## 10. Converting `sk-llm/1`

`convert_sk_llm1(row, origin, input_class)` upgrades both sk-llm/1 generations. `origin` (`path`, `file_sha256`, optional `line`) is required (S10).

**Run scope.** `source.run_id = run_id "/" arm "/" session_id`, omitting null or empty parts (`exchange_v1.sk_llm1_run_scope`, identical to the recorder's `run_scope`). Example: `run1/iso/run1:iso:A3`. The original `run_id`, `arm`, `session_id` and `role` are kept in `source.origin` (`source_run_id`, `arm`, `session_id`, `role`).

| sk-llm/1 row | v1 result |
|---|---|
| raw-generation row with `body_utf8`/`body_b64` | bytes copied verbatim, `body_fidelity` as recorded (`raw`, or `reserialized` for a replay/cache row served from a pre-raw source), headers as recorded, `redaction` as recorded. `rec_id` identical to the row's when it has a `logical_call_id` |
| raw-generation replay/cache row served from a pre-raw source and written before the writer stored those bytes (`body_sha256` set, `body_utf8`/`body_b64` null) | `body_utf8 = json.dumps(body)`, the bytes RRTransport served and hashed (checked against `body_sha256`), `reserialized`. `rec_id` identical to the row's |
| raw-generation row whose body had a sensitive-key diff (no stored bytes) | no body, `replay_class: none` (S12) |
| legacy row, parsed `body` dict | `body_utf8 = json.dumps(body)` (Python defaults, the exact bytes RRTransport serves when replaying a pre-raw row), `body_fidelity: reserialized`, `truncated: false` |
| legacy HTTP row with `body: null` | `body_utf8 = ""`, `reserialized`, `replay_class: full`: the pre-raw recorder wrote `null` for an empty upstream body, RRTransport replays it as `b""`, and the raw generation stores `body_utf8 ""` for the same response, so both generations get the same `body_sha256` |
| legacy row, `body = {"unparsed_body": text}` | `body_utf8 = text` (the stored prefix, not a JSON wrapper the wire never carried), `reserialized`; if `len(text) ≥ 4000`: `truncated: true`, `body_truncated`, `replay_class: none`. Transport classification only |
| `error_class` timeout / transport_error (either generation) | `http_status: null`, no body, `transport_error: {class, detail}` with `detail` = the legacy `{"error": "<ExceptionName>"}` value |
| any stored body containing `Bearer [redacted]` / `sk-[redacted]` / a `SENSITIVE` key = `"[redacted]"` | `body_secret_scrubbed` / `body_sensitive_key_redacted`, `replay_class: none` (S13) |
| any stored body with a member whose decoded name is `billing_portal_url` / `balance_units` | values (any JSON type) replaced by `"[redacted]"` at the byte level (fidelity kept), `body_billing_redacted`, `replay_class: none`; `rec_id` then differs from the row's |
| response headers outside the allowlist | dropped, `headers_filtered` |

Other mappings: `source.kind = import:sk-llm/1`, `source.wire = (served_from == "live")`, `endpoint` = MARA `api.cloud.mara.com/v1/chat/completions`, `case.task = task_hint`, `chain.step_kind = initial` for attempt 0 else `retry` (sk-llm/1 does not distinguish further), `chain.next_step_of = null`, `request.body` = the stored subset (`body_scope: subset`), `request_messages_digested` when `messages_sha256` replaced `messages`, `auth_header_dropped` for legacy live rows, `captured_at` from `start_ns`, `usage` = `response.usage`, `chain.agent` = `agent` with `""` mapped to null.

## 11. Golden fixtures

| File | Shape | replay_class |
|---|---|---|
| 01-content-json | 200, JSON object in `content`, reasoning present | full |
| 02-fenced-json-prose-preamble | 200, prose then fenced ```json | full |
| 03-reasoning-only-length | 200, `content: ""`, reasoning only, `finish_reason: length` | full |
| 04-forced-tool-call-string-args | 200, forced `submit_facts`, stringified arguments | full |
| 05-tool-call-object-args | 200, forced `submit_facts`, object arguments | full |
| 06-http400-did-not-complete | 400 "did not complete ... max_tokens" (tool_choice auto) | full |
| 07-http402-secret-scrubbed | 402 with `sk-[redacted]` and billing fields redacted | none |
| 08-legacy-502-truncated | converted legacy sk-llm/1 row, 4000-char `unparsed_body` | none |
| 09-http400-tool-choice-error-model-output | 400 tool_choice unsatisfied with `error_model_output` | full |
| 10-http400-json-truncation-downgrade | 400 JSON truncation, attempt 1 `downgrade` of 09 (`next_step_of`, same `logical_call_id`, its own `x-request-id`) | full |
| 11-raw-timeout-transport-error | converted raw-generation timeout row, no body | full |
| 12-carrier-intact-reasoning-redacted | reasoning + reasoning_tokens replaced | carrier_intact |
| 13-non-utf8-body-b64 | 502 latin-1 page in `body_b64` | full |

All inputs are synthetic. `expected.json` lists `rec_id`, `replay_class`, `replayable`, `body_sha256`, `body_fidelity` and `http_status` per fixture. Regenerate with `python3 rust/crates/seocho-llm-conformance/python/make_exchange_fixtures.py`; `--check` and the test suite fail on drift.

## 12. Versioning

All objects except `request.body` and `usage` are closed, so any new key, enum value or redaction token is a new schema version (`seocho.llm_exchange.v2`). Changing the `rec_id` material, the body encoding or the replay key is always a new version.
